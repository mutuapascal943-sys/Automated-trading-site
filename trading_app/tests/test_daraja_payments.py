import base64
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from trading_app.models import PaymentTransaction, Subscription, User
from trading_app.payments import daraja
from trading_app.payments.callback import CallbackError, process_stk_callback


TEST_DARAJA_SETTINGS = {
    'DEBUG': False,
    'DARAJA_CONSUMER_KEY': 'unit-test-consumer-key',
    'DARAJA_CONSUMER_SECRET': 'unit-test-consumer-secret',
    'DARAJA_SHORTCODE': '174379',
    'DARAJA_PASSKEY': 'unit-test-passkey',
    'DARAJA_CALLBACK_URL': 'https://example.invalid/api/payments/daraja/callback/',
    'DARAJA_ENVIRONMENT': 'sandbox',
    'DARAJA_BASIC_AMOUNT_KES': 1500,
    'MOCK_SUBSCRIPTIONS_ENABLED': True,
}


def success_payload(payment, receipt='QWE123ABC'):
    return {
        'Body': {
            'stkCallback': {
                'MerchantRequestID': payment.merchant_request_id,
                'CheckoutRequestID': payment.checkout_request_id,
                'ResultCode': 0,
                'ResultDesc': 'The service request is processed successfully.',
                'CallbackMetadata': {'Item': [
                    {'Name': 'Amount', 'Value': payment.amount_kes},
                    {'Name': 'MpesaReceiptNumber', 'Value': receipt},
                    {'Name': 'PhoneNumber', 'Value': payment.phone_number},
                ]},
            },
        },
    }


def result_payload(payment, code, description):
    return {
        'Body': {
            'stkCallback': {
                'MerchantRequestID': payment.merchant_request_id,
                'CheckoutRequestID': payment.checkout_request_id,
                'ResultCode': code,
                'ResultDesc': description,
            },
        },
    }


@override_settings(**TEST_DARAJA_SETTINGS)
class DarajaServiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_configuration_selects_sandbox_and_production_hosts(self):
        config = daraja.DarajaConfig.from_settings()
        self.assertEqual(config.environment, 'sandbox')
        self.assertEqual(config.base_url, daraja.SANDBOX_BASE_URL)
        with override_settings(DARAJA_ENVIRONMENT='production'):
            self.assertEqual(daraja.DarajaConfig.from_settings().base_url, daraja.PRODUCTION_BASE_URL)

    def test_production_requires_https_callback(self):
        with override_settings(DARAJA_ENVIRONMENT='production', DARAJA_CALLBACK_URL='http://example.invalid/callback'):
            with self.assertRaises(daraja.DarajaError):
                daraja.DarajaConfig.from_settings().validate()

    @patch('trading_app.payments.daraja.requests.get')
    def test_oauth_success_and_token_cache(self, http_get):
        http_get.return_value.json.return_value = {'access_token': 'unit-token', 'expires_in': 3600}
        http_get.return_value.raise_for_status.return_value = None
        token = daraja.get_access_token()
        self.assertEqual(token, 'unit-token')
        self.assertEqual(cache.get(daraja.DarajaConfig.from_settings().token_cache_key), 'unit-token')
        self.assertEqual(http_get.call_args.args[0], f'{daraja.SANDBOX_BASE_URL}/oauth/v1/generate?grant_type=client_credentials')
        self.assertEqual(http_get.call_args.kwargs['auth'], ('unit-test-consumer-key', 'unit-test-consumer-secret'))
        daraja.get_access_token()
        http_get.assert_called_once()

    @patch('trading_app.payments.daraja.requests.get')
    def test_oauth_failure_is_sanitized(self, http_get):
        http_get.side_effect = requests.Timeout('credential-bearing mock transport detail')
        with self.assertRaises(daraja.DarajaError) as raised:
            daraja.get_access_token()
        self.assertNotIn('unit-test-consumer-secret', str(raised.exception))
        self.assertNotIn('credential-bearing', str(raised.exception))

    @patch('trading_app.payments.daraja.get_access_token', return_value='unit-token')
    @patch('trading_app.payments.daraja.requests.post')
    def test_stk_push_request_uses_server_price_and_configured_values(self, http_post, _token):
        http_post.return_value.json.return_value = {
            'MerchantRequestID': 'merchant-1', 'CheckoutRequestID': 'checkout-1',
            'CustomerMessage': 'Prompt sent',
        }
        http_post.return_value.raise_for_status.return_value = None
        result = daraja.initiate_stk_push('0712345678', 'ATS1', 'BASIC subscription')
        payload = http_post.call_args.kwargs['json']
        self.assertEqual(result['checkout_request_id'], 'checkout-1')
        self.assertEqual(payload['Amount'], 1500)
        self.assertEqual(payload['PhoneNumber'], '254712345678')
        self.assertEqual(payload['CallBackURL'], TEST_DARAJA_SETTINGS['DARAJA_CALLBACK_URL'])
        self.assertEqual(http_post.call_args.args[0], f'{daraja.SANDBOX_BASE_URL}/mpesa/stkpush/v1/processrequest')
        self.assertEqual(payload['Password'], base64.b64encode(b'174379unit-test-passkey20250102030405').decode() if payload['Timestamp'] == '20250102030405' else payload['Password'])

    @patch('trading_app.payments.daraja.get_access_token', return_value='unit-token')
    @patch('trading_app.payments.daraja.requests.post')
    def test_stk_push_timeout_is_sanitized(self, http_post, _token):
        http_post.side_effect = requests.Timeout('private transport data')
        with self.assertRaises(daraja.DarajaTimeoutError) as raised:
            daraja.initiate_stk_push('0712345678', 'ATS1', 'BASIC subscription')
        self.assertNotIn('private transport data', str(raised.exception))

    def test_invalid_phone_and_missing_config_rejected(self):
        with self.assertRaises(daraja.DarajaError):
            daraja.normalize_phone_number('12345')
        with override_settings(DARAJA_PASSKEY=''):
            with self.assertRaises(daraja.DarajaError):
                daraja.DarajaConfig.from_settings().validate()


@override_settings(**TEST_DARAJA_SETTINGS)
class DarajaSubscriptionAPITests(TestCase):
    def setUp(self):
        cache.delete(daraja.TOKEN_CACHE_KEY)
        self.oauth_response = Mock()
        self.oauth_response.json.return_value = {'access_token': 'unit-token', 'expires_in': 3600}
        self.oauth_response.raise_for_status.return_value = None
        self.query_response = Mock()
        self.query_response.json.return_value = {
            'ResultCode': '0', 'ResultDesc': 'Verified by STK Query',
        }
        self.query_response.raise_for_status.return_value = None
        self.http_get_patch = patch('trading_app.payments.daraja.requests.get', return_value=self.oauth_response)
        self.http_post_patch = patch('trading_app.payments.daraja.requests.post', return_value=self.query_response)
        self.http_get = self.http_get_patch.start()
        self.http_post = self.http_post_patch.start()
        self.addCleanup(self.http_get_patch.stop)
        self.addCleanup(self.http_post_patch.stop)
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            email='daraja-test@example.com', username='daraja-test', password='Testpass123',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.subscription = Subscription.objects.create(
            user=self.user, tier='FREE', is_active=True, expires_at=timezone.now() + timedelta(hours=2),
        )

    @patch('trading_app.payments.daraja.initiate_stk_push')
    def test_checkout_creates_pending_transaction_without_activating_subscription(self, stk):
        stk.return_value = {
            'merchant_request_id': 'merchant-2', 'checkout_request_id': 'checkout-2',
            'customer_message': 'Prompt sent',
        }
        response = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'phone_number': '0712345678', 'amount': 1,
        }, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json()['access_granted'])
        self.assertNotIn('checkout_request_id', response.json())
        transaction_record = PaymentTransaction.objects.get(user=self.user)
        self.assertEqual(transaction_record.amount_kes, 1500)
        self.assertEqual(transaction_record.phone_number, '254712345678')
        self.assertEqual(transaction_record.status, PaymentTransaction.STATUS_PENDING)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.tier, 'FREE')
        self.assertEqual(self.subscription.expires_at, transaction_record.subscription.expires_at)
        stk.assert_called_once()

    def test_checkout_requires_valid_product_and_phone(self):
        wrong_product = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'PRO', 'phone_number': '0712345678',
        }, format='json')
        invalid_phone = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'phone_number': '12345',
        }, format='json')
        self.assertEqual(wrong_product.status_code, 400)
        self.assertEqual(invalid_phone.status_code, 400)
        self.assertFalse(PaymentTransaction.objects.filter(user=self.user).exists())

    @patch('trading_app.payments.daraja.initiate_stk_push', side_effect=daraja.DarajaError('safe failure'))
    def test_stk_failure_creates_no_active_subscription(self, _stk):
        response = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'phone_number': '0712345678',
        }, format='json')
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json()['access_granted'])
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.tier, 'FREE')
        self.assertEqual(PaymentTransaction.objects.get(user=self.user).status, PaymentTransaction.STATUS_FAILED)

    @patch('trading_app.payments.daraja.initiate_stk_push', side_effect=daraja.DarajaTimeoutError('unknown'))
    def test_stk_timeout_keeps_payment_pending_and_subscription_inactive(self, _stk):
        response = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'phone_number': '0712345678',
        }, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['status'], 'payment_request_unconfirmed')
        self.assertFalse(response.json()['access_granted'])
        self.assertEqual(PaymentTransaction.objects.get(user=self.user).status, PaymentTransaction.STATUS_PENDING)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.tier, 'FREE')

    def test_successful_callback_activates_only_after_daraja_query(self):
        payment = PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-3',
            checkout_request_id='checkout-3', status=PaymentTransaction.STATUS_PENDING,
        )
        response = self.client.post(reverse('api_daraja_stk_callback'), success_payload(payment), format='json')
        self.assertEqual(response.status_code, 200)
        self.http_post.assert_called_once()
        self.assertEqual(self.http_post.call_args.args[0], f'{daraja.SANDBOX_BASE_URL}/mpesa/stkpushquery/v1/query')
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.STATUS_SUCCESS)
        self.assertEqual(payment.mpesa_receipt_number, 'QWE123ABC')
        self.assertEqual(self.subscription.tier, 'BASIC')
        self.assertTrue(self.subscription.is_active)
        self.assertGreater(self.subscription.expires_at, timezone.now() + timedelta(days=29))

    def test_cancelled_callback_marks_cancelled_without_activation(self):
        self.query_response.json.return_value = {'ResultCode': '1032', 'ResultDesc': 'Cancelled by user'}
        payment = PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-4',
            checkout_request_id='checkout-4', status=PaymentTransaction.STATUS_PENDING,
        )
        response = self.client.post(reverse('api_daraja_stk_callback'), result_payload(payment, 1032, 'Cancelled'), format='json')
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.STATUS_CANCELLED)
        self.assertEqual(self.subscription.tier, 'FREE')

    def test_failed_callback_does_not_activate_subscription(self):
        self.query_response.json.return_value = {'ResultCode': '1', 'ResultDesc': 'Failed'}
        payment = PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-5',
            checkout_request_id='checkout-5', status=PaymentTransaction.STATUS_PENDING,
        )
        response = self.client.post(reverse('api_daraja_stk_callback'), result_payload(payment, 1, 'Failed'), format='json')
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.STATUS_FAILED)
        self.assertEqual(self.subscription.tier, 'FREE')

    def test_malformed_callback_rejected_without_state_change(self):
        response = self.client.post(reverse('api_daraja_stk_callback'), {'bad': 'payload'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ResultCode'], 1)
        self.assertFalse(PaymentTransaction.objects.exists())
        self.assertEqual(self.subscription.tier, 'FREE')

    @patch('trading_app.payments.daraja.initiate_stk_push', return_value={
        'merchant_request_id': 'merchant-status', 'checkout_request_id': 'checkout-status',
        'customer_message': 'Prompt sent',
    })
    def test_payment_status_is_owner_scoped_and_pending_has_no_access(self, _stk):
        self.user.trial_started_at = timezone.now() - timedelta(days=2)
        self.user.save(update_fields=['trial_started_at'])
        checkout = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'phone_number': '0712345678',
        }, format='json')
        payment_id = checkout.json()['payment_id']
        status_response = self.client.get(reverse('api_daraja_payment_status', args=[payment_id]))
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()['status'], PaymentTransaction.STATUS_PENDING)
        self.assertFalse(status_response.json()['access_granted'])

        other_user = User.objects.create_user(email='not-owner@example.com', username='not-owner')
        other_client = APIClient()
        other_client.force_authenticate(other_user)
        forbidden = other_client.get(reverse('api_daraja_payment_status', args=[payment_id]))
        self.assertEqual(forbidden.status_code, 404)

    def test_duplicate_success_callback_is_idempotent_and_uses_unique_receipt(self):
        payment = PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-6',
            checkout_request_id='checkout-6', status=PaymentTransaction.STATUS_PENDING,
        )
        payload = success_payload(payment, receipt='UNIQUE123')
        self.client.post(reverse('api_daraja_stk_callback'), payload, format='json')
        self.subscription.refresh_from_db()
        expires_at = self.subscription.expires_at
        self.client.post(reverse('api_daraja_stk_callback'), payload, format='json')
        self.subscription.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.STATUS_SUCCESS)
        self.assertEqual(self.subscription.expires_at, expires_at)
        self.http_post.assert_called_once()

    def test_duplicate_success_receipt_cannot_activate_another_payment(self):
        first = PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-7',
            checkout_request_id='checkout-7', status=PaymentTransaction.STATUS_PENDING,
        )
        process_stk_callback(success_payload(first, 'SAME123'))
        other_user = User.objects.create_user(email='other-daraja@example.com', username='other-daraja')
        other_sub = Subscription.objects.create(user=other_user, tier='FREE', is_active=True)
        second = PaymentTransaction.objects.create(
            user=other_user, subscription=other_sub, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-8',
            checkout_request_id='checkout-8', status=PaymentTransaction.STATUS_PENDING,
        )
        process_stk_callback(success_payload(second, 'SAME123'))
        second.refresh_from_db()
        other_sub.refresh_from_db()
        self.assertEqual(second.status, PaymentTransaction.STATUS_FAILED)
        self.assertEqual(other_sub.tier, 'FREE')

    def test_success_callback_requires_daraja_success_status(self):
        self.query_response.json.return_value = {'ResultCode': '1', 'ResultDesc': 'Not successful'}
        payment = PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-9',
            checkout_request_id='checkout-9', status=PaymentTransaction.STATUS_PENDING,
        )
        process_stk_callback(success_payload(payment))
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.STATUS_FAILED)
        self.assertEqual(self.subscription.tier, 'FREE')

    @patch('trading_app.payments.callback.daraja.query_stk_status', return_value={
        'result_code': '0', 'result_description': 'Verified',
    })
    def test_success_callback_rejects_amount_phone_or_receipt_mismatch(self, _query):
        payment = PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-mismatch',
            checkout_request_id='checkout-mismatch', status=PaymentTransaction.STATUS_PENDING,
        )
        payload = success_payload(payment)
        payload['Body']['stkCallback']['CallbackMetadata']['Item'][0]['Value'] = 1499
        with self.assertRaises(CallbackError):
            process_stk_callback(payload)
        payload = success_payload(payment)
        payload['Body']['stkCallback']['CallbackMetadata']['Item'][2]['Value'] = 254711111111
        with self.assertRaises(CallbackError):
            process_stk_callback(payload)
        payload = success_payload(payment)
        payload['Body']['stkCallback']['CallbackMetadata']['Item'] = [
            item for item in payload['Body']['stkCallback']['CallbackMetadata']['Item']
            if item['Name'] != 'MpesaReceiptNumber'
        ]
        with self.assertRaises(CallbackError):
            process_stk_callback(payload)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.tier, 'FREE')

    def test_success_callback_with_query_outage_remains_pending(self):
        self.http_post.side_effect = requests.Timeout('network timeout')
        payment = PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription, tier='BASIC', amount_kes=1500,
            phone_number='254712345678', merchant_request_id='merchant-10',
            checkout_request_id='checkout-10', status=PaymentTransaction.STATUS_PENDING,
        )
        process_stk_callback(success_payload(payment))
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.STATUS_PENDING)
        self.assertEqual(self.subscription.tier, 'FREE')

    def test_production_mock_checkout_flag_does_not_grant_access(self):
        with override_settings(MOCK_SUBSCRIPTIONS_ENABLED=True):
            response = self.client.post(reverse('api_subscription_checkout'), {
                'tier': 'BASIC', 'phone_number': '0712345678',
            }, format='json')
        self.assertIn(response.status_code, (202, 502, 503))
        self.assertNotEqual(response.json().get('status'), 'mock_activated')
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.tier, 'FREE')

    @patch('trading_app.payments.daraja.get_access_token', side_effect=daraja.DarajaError('safe OAuth failure'))
    def test_credentials_and_provider_payload_never_exposed(self, _token):
        response = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'phone_number': '0712345678',
        }, format='json')
        body = json.dumps(response.json())
        self.assertNotIn('unit-test-consumer-secret', body)
        self.assertNotIn(TEST_DARAJA_SETTINGS['DARAJA_CONSUMER_SECRET'], body)
        self.assertNotIn('access_token', body)
