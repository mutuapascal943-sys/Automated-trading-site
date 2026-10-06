from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from trading_app.models import PaymentTransaction
from trading_app.payments import daraja

logger = logging.getLogger(__name__)

CANCELLED_RESULT_CODES = {'1032', '1037'}


class CallbackError(ValueError):
    pass


def _metadata_items(callback: dict) -> dict[str, object]:
    body = callback.get('Body')
    if not isinstance(body, dict):
        raise CallbackError('Callback body is missing.')
    stk = body.get('stkCallback')
    if not isinstance(stk, dict):
        raise CallbackError('STK callback is missing.')
    metadata = stk.get('CallbackMetadata')
    if not isinstance(metadata, dict) or not isinstance(metadata.get('Item'), list):
        raise CallbackError('Callback metadata is missing.')
    items: dict[str, object] = {}
    for item in metadata['Item']:
        if isinstance(item, dict) and isinstance(item.get('Name'), str) and 'Value' in item:
            items[item['Name']] = item['Value']
    return items


def _parse_callback(payload: object) -> tuple[str, str, str, str, dict[str, object]]:
    if not isinstance(payload, dict):
        raise CallbackError('Callback payload must be an object.')
    body = payload.get('Body')
    stk = body.get('stkCallback') if isinstance(body, dict) else None
    if not isinstance(stk, dict):
        raise CallbackError('STK callback is missing.')
    merchant_id = stk.get('MerchantRequestID')
    checkout_id = stk.get('CheckoutRequestID')
    result_code = stk.get('ResultCode')
    result_description = stk.get('ResultDesc')
    if not all(isinstance(value, (str, int)) for value in (merchant_id, checkout_id, result_code)):
        raise CallbackError('Callback identifiers or result code are invalid.')
    if not isinstance(result_description, str):
        result_description = ''
    metadata = _metadata_items(payload) if str(result_code) == '0' else {}
    return str(merchant_id), str(checkout_id), str(result_code), result_description[:255], metadata


def process_stk_callback(payload: object, status_query=daraja.query_stk_status) -> str:
    merchant_id, checkout_id, result_code, result_description, metadata = _parse_callback(payload)
    with transaction.atomic():
        payment = PaymentTransaction.objects.select_for_update().filter(
            checkout_request_id=checkout_id,
            merchant_request_id=merchant_id,
        ).select_related('subscription', 'user').first()
        if payment is None:
            raise CallbackError('No matching payment request exists.')
        if payment.status in {
            PaymentTransaction.STATUS_SUCCESS,
            PaymentTransaction.STATUS_FAILED,
            PaymentTransaction.STATUS_CANCELLED,
        }:
            return payment.status

        try:
            verified = status_query(checkout_id)
        except daraja.DarajaError:
            payment.status = PaymentTransaction.STATUS_PENDING
            payment.result_code = 'VERIFICATION_PENDING'
            payment.result_description = 'Daraja confirmation is temporarily unavailable.'
            payment.save(update_fields=['status', 'result_code', 'result_description'])
            return payment.status

        verified_code = str(verified['result_code'])
        verified_description = str(verified.get('result_description') or '')[:255]
        if verified_code != '0':
            payment.result_code = verified_code[:20]
            payment.result_description = verified_description
            payment.status = (
                PaymentTransaction.STATUS_CANCELLED
                if verified_code in CANCELLED_RESULT_CODES
                else PaymentTransaction.STATUS_FAILED
            )
            payment.completed_at = timezone.now()
            payment.save(update_fields=['result_code', 'result_description', 'status', 'completed_at'])
            return payment.status

        if result_code != '0':
            payment.status = PaymentTransaction.STATUS_PENDING
            payment.result_code = 'AWAITING_SUCCESS_CALLBACK'
            payment.result_description = 'Daraja confirms success; awaiting matching success metadata.'
            payment.save(update_fields=['status', 'result_code', 'result_description'])
            return payment.status

        receipt = metadata.get('MpesaReceiptNumber')
        amount_raw = metadata.get('Amount')
        phone_raw = metadata.get('PhoneNumber')
        try:
            amount = Decimal(str(amount_raw))
        except (InvalidOperation, TypeError, ValueError):
            raise CallbackError('Successful callback amount is invalid.') from None
        if not isinstance(receipt, str) or not receipt or len(receipt) > 32:
            raise CallbackError('Successful callback receipt is invalid.')
        if amount != Decimal(payment.amount_kes):
            raise CallbackError('Successful callback amount does not match the initiated payment.')
        try:
            phone = daraja.normalize_phone_number(phone_raw)
        except daraja.DarajaError:
            raise CallbackError('Successful callback phone number is invalid.') from None
        if phone != payment.phone_number:
            raise CallbackError('Successful callback phone number does not match the initiated payment.')
        receipt = metadata.get('MpesaReceiptNumber')
        duplicate_receipt = PaymentTransaction.objects.filter(
            mpesa_receipt_number=receipt,
        ).exclude(pk=payment.pk).exists()
        if duplicate_receipt:
            payment.status = PaymentTransaction.STATUS_FAILED
            payment.result_code = 'DUPLICATE_RECEIPT'
            payment.result_description = 'Receipt is already associated with another payment.'
            payment.completed_at = timezone.now()
            payment.save(update_fields=['status', 'result_code', 'result_description', 'completed_at'])
            return payment.status
        now = timezone.now()
        subscription = payment.subscription.__class__.objects.select_for_update().get(pk=payment.subscription_id)
        if (
            subscription.tier == payment.tier
            and subscription.is_active
            and subscription.expires_at
            and subscription.expires_at > now
        ):
            start_at = subscription.expires_at
        else:
            start_at = now
        subscription.tier = payment.tier
        subscription.is_active = True
        subscription.started_at = now
        subscription.expires_at = start_at + timedelta(days=30)
        subscription.save(update_fields=['tier', 'is_active', 'started_at', 'expires_at'])

        payment.result_code = verified_code
        payment.result_description = 'Daraja transaction verified successfully.'
        payment.mpesa_receipt_number = receipt
        payment.status = PaymentTransaction.STATUS_SUCCESS
        payment.completed_at = now
        payment.save(update_fields=[
            'result_code', 'result_description', 'mpesa_receipt_number',
            'status', 'completed_at',
        ])
        return payment.status
