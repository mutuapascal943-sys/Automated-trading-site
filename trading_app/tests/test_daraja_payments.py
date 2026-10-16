from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from trading_app.models import PaymentTransaction, Subscription, User
from trading_app.payments import daraja


class ManualMpesaSubscriptionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='mpesatester', email='mpesa@test.com', password='password123', email_verified=True,
        )
        self.client.force_authenticate(user=self.user)
        self.subscription = Subscription.objects.create(
            user=self.user, tier='FREE', is_active=True, expires_at=timezone.now() + timedelta(hours=2),
        )

    def test_manual_checkout_creates_pending_transaction(self):
        response = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'transaction_reference': 'QHX892LKP1',
        }, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json()['access_granted'])
        transaction_record = PaymentTransaction.objects.get(user=self.user)
        self.assertEqual(transaction_record.amount_kes, settings.DARAJA_BASIC_AMOUNT_KES)
        self.assertEqual(transaction_record.phone_number, '0705794964')
        self.assertEqual(transaction_record.mpesa_receipt_number, 'QHX892LKP1')
        self.assertEqual(transaction_record.status, PaymentTransaction.STATUS_PENDING)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.tier, 'FREE')

    def test_duplicate_transaction_reference_rejected(self):
        PaymentTransaction.objects.create(
            user=self.user, subscription=self.subscription,
            tier='BASIC', amount_kes=settings.DARAJA_BASIC_AMOUNT_KES, phone_number='0705794964',
            mpesa_receipt_number='QHX892LKP1', status=PaymentTransaction.STATUS_PENDING,
        )
        response = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'transaction_reference': 'QHX892LKP1',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('already been submitted', response.json()['error'])

    def test_checkout_requires_valid_product_and_reference(self):
        wrong_product = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'PRO', 'transaction_reference': 'QHX892LKP1',
        }, format='json')
        no_ref = self.client.post(reverse('api_subscription_checkout'), {
            'tier': 'BASIC', 'transaction_reference': '',
        }, format='json')
        self.assertEqual(wrong_product.status_code, 400)
        self.assertEqual(no_ref.status_code, 400)
