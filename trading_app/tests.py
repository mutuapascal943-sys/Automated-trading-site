import time
import json
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model
from .models import (
    User, EmailOTP, Trade, TradingSignal,
    RAGDocument, RAGChunk, LLMQuery, Subscription,
    SecurityQuestion,
)
from .services.email_service import generate_otp, send_otp_email
from .services.cache_service import CacheService
from .forms import (
    PasswordResetRequestForm, PasswordResetVerifyForm,
    SetNewPasswordForm, ProfileForm, ProfilePictureForm,
    RegisterForm,
)
from .services.rag_engine import RAGEngine
from .services.llm_service import LLMService
from .services.broker_service import BrokerService

User = get_user_model()


class UserModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            username='testuser',
            password='testpass123',
            broker='Deriv',
        )

    def test_create_user(self):
        self.assertEqual(self.user.email, 'test@example.com')
        self.assertEqual(self.user.broker, 'Deriv')
        self.assertTrue(self.user.check_password('testpass123'))
        self.assertFalse(self.user.two_factor_enabled)
        self.assertEqual(self.user.balance, Decimal('12430.00'))

    def test_user_str(self):
        self.assertEqual(str(self.user), 'test@example.com')

    def test_user_with_broker_api_fields(self):
        self.user.broker_api_key = 'test_api_key'
        self.user.broker_api_secret = 'test_api_secret'
        self.user.broker_account_id = 'acc_123'
        self.user.save()
        updated = User.objects.get(id=self.user.id)
        self.assertEqual(updated.broker_api_key, 'test_api_key')
        self.assertEqual(updated.broker_account_id, 'acc_123')

    def test_daily_trade_count_reset(self):
        self.user.daily_trades_count = 10
        self.user.last_trade_date = timezone.now().date()
        self.user.save()
        self.assertEqual(User.objects.get(id=self.user.id).daily_trades_count, 10)

    def test_user_indexes(self):
        indexes = [idx.fields for idx in User._meta.indexes]
        self.assertIn(['email'], indexes)
        self.assertIn(['two_factor_enabled'], indexes)


class EmailOTPModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='otp@example.com', username='otpuser', password='pass123'
        )
        self.otp = EmailOTP.objects.create(
            user=self.user,
            code='123456',
            purpose='2fa',
            expires_at=timezone.now() + timedelta(seconds=300),
        )

    def test_create_otp(self):
        self.assertEqual(self.otp.code, '123456')
        self.assertEqual(self.otp.purpose, '2fa')
        self.assertFalse(self.otp.is_used)

    def test_otp_is_valid(self):
        self.assertTrue(self.otp.is_valid())

    def test_otp_expired(self):
        self.otp.expires_at = timezone.now() - timedelta(seconds=1)
        self.otp.save()
        self.assertTrue(self.otp.is_expired())
        self.assertFalse(self.otp.is_valid())

    def test_otp_used(self):
        self.otp.is_used = True
        self.otp.save()
        self.assertFalse(self.otp.is_valid())

    def test_otp_str(self):
        self.assertIn('otp@example.com', str(self.otp))


class EmailOTPServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='service@example.com', username='serviceuser', password='pass123'
        )

    def test_generate_otp(self):
        code = generate_otp()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_generate_otp_multiple(self):
        codes = [generate_otp() for _ in range(100)]
        self.assertEqual(len(set(codes)), len(codes),
                         'OTP codes should be unique')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
    )
    def test_send_otp_email(self):
        from django.core import mail
        result = send_otp_email(self.user, '123456')
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('123456', mail.outbox[0].body)
        self.assertIn(self.user.email, mail.outbox[0].to)


class CacheServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='cache@example.com', username='cacheuser', password='pass123'
        )

    def test_set_and_get_otp(self):
        CacheService.set_otp(self.user.id, '654321')
        self.assertEqual(CacheService.get_otp(self.user.id), '654321')

    def test_verify_otp_correct(self):
        CacheService.set_otp(self.user.id, '111111')
        self.assertTrue(CacheService.verify_otp(self.user.id, '111111'))
        self.assertIsNone(CacheService.get_otp(self.user.id))

    def test_verify_otp_wrong(self):
        CacheService.set_otp(self.user.id, '222222')
        self.assertFalse(CacheService.verify_otp(self.user.id, '999999'))
        self.assertEqual(CacheService.get_otp(self.user.id), '222222')

    def test_delete_otp(self):
        CacheService.set_otp(self.user.id, '333333')
        CacheService.delete_otp(self.user.id)
        self.assertIsNone(CacheService.get_otp(self.user.id))

    def test_rate_limit(self):
        self.assertTrue(CacheService.check_rate_limit('test_key', max_attempts=3, window=60))
        self.assertTrue(CacheService.check_rate_limit('test_key', max_attempts=3, window=60))
        self.assertTrue(CacheService.check_rate_limit('test_key', max_attempts=3, window=60))
        self.assertFalse(CacheService.check_rate_limit('test_key', max_attempts=3, window=60))

    def test_rate_limit_reset(self):
        CacheService.check_rate_limit('reset_key', max_attempts=1, window=60)
        self.assertFalse(CacheService.check_rate_limit('reset_key', max_attempts=1, window=60))
        CacheService.reset_rate_limit('reset_key')
        self.assertTrue(CacheService.check_rate_limit('reset_key', max_attempts=1, window=60))

    def test_market_data_cache(self):
        data = {'price': 1.08, 'change': 0.5}
        CacheService.set_market_data('EUR/USD', data)
        self.assertEqual(CacheService.get_market_data('EUR/USD'), data)


class RAGEngineTests(TestCase):
    def setUp(self):
        self.engine = RAGEngine()

    def test_chunk_text_small(self):
        text = 'This is a small text.'
        chunks = self.engine.chunk_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]['text'], text)

    def test_chunk_text_large(self):
        text = 'Paragraph one. ' * 200 + '\n\n' + 'Paragraph two. ' * 200
        chunks = self.engine.chunk_text(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertIn('id', chunk)
            self.assertIn('text', chunk)
            self.assertIn('start_pos', chunk)
            self.assertIn('end_pos', chunk)

    def test_chunk_id_uniqueness(self):
        text = 'Hello world. ' * 50
        chunks = self.engine.chunk_text(text)
        ids = [c['id'] for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_cosine_similarity(self):
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(self.engine.cosine_similarity(a, b), 1.0)

    def test_cosine_similarity_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(self.engine.cosine_similarity(a, b), 0.0)

    def test_cosine_similarity_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        self.assertEqual(self.engine.cosine_similarity(a, b), 0.0)

    def test_process_document(self):
        chunks = self.engine.process_document(
            title='Test Doc',
            content='Test content here. ' * 50,
            source='manual',
        )
        for chunk in chunks:
            self.assertEqual(chunk['title'], 'Test Doc')
            self.assertEqual(chunk['source'], 'manual')

    def test_chunk_respects_chunk_size(self):
        original_size = self.engine.chunk_size
        self.engine.chunk_size = 100
        text = 'A' * 500
        chunks = self.engine.chunk_text(text)
        self.assertGreater(len(chunks), 1)
        self.engine.chunk_size = original_size


class TradeModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='trade@example.com', username='tradeuser', password='pass123'
        )
        self.trade = Trade.objects.create(
            user=self.user,
            symbol='EUR/USD',
            action='BUY',
            volume=0.1,
            entry_price=Decimal('1.08000'),
            stop_loss=Decimal('1.07500'),
            take_profit=Decimal('1.08500'),
            status='OPEN',
        )

    def test_create_trade(self):
        self.assertEqual(self.trade.symbol, 'EUR/USD')
        self.assertEqual(self.trade.action, 'BUY')
        self.assertEqual(float(self.trade.volume), 0.1)
        self.assertEqual(self.trade.status, 'OPEN')

    def test_close_trade_with_pnl(self):
        self.trade.exit_price = Decimal('1.08600')
        self.trade.pnl = Decimal('60.00')
        self.trade.status = 'CLOSED'
        self.trade.closed_at = timezone.now()
        self.trade.save()

        closed = Trade.objects.get(id=self.trade.id)
        self.assertEqual(closed.status, 'CLOSED')
        self.assertEqual(float(closed.pnl), 60.00)

    def test_trade_str(self):
        self.assertIn('EUR/USD BUY', str(self.trade))

    def test_trade_indexes(self):
        indexes = [idx.fields for idx in Trade._meta.indexes]
        self.assertIn(['user', 'status'], indexes)
        self.assertIn(['symbol'], indexes)
        self.assertIn(['created_at'], indexes)


class TradingSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='signal@example.com', username='signaluser', password='pass123'
        )
        self.signal = TradingSignal.objects.create(
            user=self.user,
            symbol='GBP/USD',
            signal_type='BUY',
            confidence=85,
            entry_price=Decimal('1.27000'),
            stop_loss=Decimal('1.26500'),
            take_profit=Decimal('1.27800'),
            reasoning='Strong bullish pattern detected',
            risk_level='MEDIUM',
        )

    def test_signal_creation(self):
        self.assertEqual(self.signal.confidence, 85)
        self.assertEqual(self.signal.risk_level, 'MEDIUM')
        self.assertFalse(self.signal.is_executed)

    def test_signal_str(self):
        self.assertIn('GBP/USD BUY', str(self.signal))


class RAGDocumentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='rag@example.com', username='raguser', password='pass123'
        )
        self.doc = RAGDocument.objects.create(
            user=self.user,
            title='Trading Strategy Guide',
            content='This is a comprehensive trading strategy document. ' * 100,
            source='manual',
            file_size=5000,
        )

    def test_document_creation(self):
        self.assertEqual(self.doc.title, 'Trading Strategy Guide')
        self.assertEqual(self.doc.source, 'manual')
        self.assertEqual(self.doc.file_size, 5000)
        self.assertFalse(self.doc.is_indexed)

    def test_document_str(self):
        self.assertEqual(str(self.doc), 'Trading Strategy Guide')

    def test_chunk_creation(self):
        chunk = RAGChunk.objects.create(
            document=self.doc,
            chunk_id='abc123',
            text='Sample chunk text',
            start_pos=0,
            end_pos=50,
        )
        self.assertEqual(chunk.chunk_id, 'abc123')
        self.assertEqual(chunk.document, self.doc)


class LLMQueryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='llm@example.com', username='llmuser', password='pass123'
        )
        self.query = LLMQuery.objects.create(
            user=self.user,
            query_type='market_analysis',
            prompt='Analyze EUR/USD',
            response='Bullish signal detected',
            tokens_used=150,
            latency_ms=1200,
            success=True,
        )

    def test_query_creation(self):
        self.assertEqual(self.query.query_type, 'market_analysis')
        self.assertTrue(self.query.success)
        self.assertEqual(self.query.tokens_used, 150)


class SubscriptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='sub@example.com', username='subuser', password='pass123'
        )

    def test_default_subscription(self):
        sub = Subscription.objects.create(user=self.user)
        self.assertEqual(sub.tier, 'FREE')
        self.assertTrue(sub.is_active)

    def test_subscription_upgrade(self):
        sub = Subscription.objects.create(user=self.user, tier='PRO')
        self.assertEqual(sub.tier, 'PRO')
        self.assertEqual(str(sub), f'{self.user.email} - PRO')


class RegistrationViewTests(TestCase):
    def test_register_page_loads(self):
        response = self.client.get(reverse('register'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/register.html')

    def test_registration_redirects_when_authenticated(self):
        self.client.force_login(
            User.objects.create_user(email='auth@test.com', username='authtest', password='pass123')
        )
        response = self.client.get(reverse('register'))
        self.assertRedirects(response, reverse('dashboard'))

    def test_registration_creates_user(self):
        response = self.client.post(reverse('register'), {
            'email': 'newuser@test.com',
            'username': 'newuser',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'broker': 'Deriv',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email='newuser@test.com').exists())

    def test_registration_creates_email_otp(self):
        self.client.post(reverse('register'), {
            'email': 'otpcheck@test.com',
            'username': 'otpcheck',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'broker': 'Exness',
        })
        user = User.objects.get(email='otpcheck@test.com')
        otps = EmailOTP.objects.filter(user=user, purpose='2fa')
        self.assertEqual(otps.count(), 1)
        self.assertFalse(otps.first().is_used)


class LoginViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='logintest@test.com', username='logintest', password='SecurePass1!'
        )

    def test_login_page_loads(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_login_success_without_2fa(self):
        response = self.client.post(reverse('login'), {
            'username': 'logintest@test.com',
            'password': 'SecurePass1!',
        })
        self.assertRedirects(response, reverse('dashboard'))

    def test_login_with_2fa_redirects(self):
        self.user.two_factor_enabled = True
        self.user.save()
        response = self.client.post(reverse('login'), {
            'username': 'logintest@test.com',
            'password': 'SecurePass1!',
        })
        self.assertRedirects(response, reverse('verify_2fa'))

    def test_login_with_2fa_creates_otp(self):
        self.user.two_factor_enabled = True
        self.user.save()
        self.client.post(reverse('login'), {
            'username': 'logintest@test.com',
            'password': 'SecurePass1!',
        })
        otps = EmailOTP.objects.filter(user=self.user, purpose='2fa', is_used=False)
        self.assertEqual(otps.count(), 1)

    def test_login_failure_wrong_password(self):
        response = self.client.post(reverse('login'), {
            'username': 'logintest@test.com',
            'password': 'WrongPass1!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please enter a correct email and password')


class TwoFactorViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='2fatest@test.com', username='2fatest', password='SecurePass1!'
        )
        self.client.force_login(self.user)

    def test_setup_2fa_page_loads(self):
        response = self.client.get(reverse('setup_2fa'))
        self.assertEqual(response.status_code, 200)

    def test_setup_2fa_sends_otp(self):
        self.client.get(reverse('setup_2fa'))
        otp = EmailOTP.objects.filter(user=self.user, purpose='2fa', is_used=False).first()
        self.assertIsNotNone(otp)
        self.assertEqual(len(otp.code), 6)

    def test_setup_2fa_verify_valid_code(self):
        self.client.get(reverse('setup_2fa'))
        otp = EmailOTP.objects.filter(user=self.user, purpose='2fa', is_used=False).first()
        CacheService.set_otp(self.user.id, otp.code)

        response = self.client.post(reverse('setup_2fa'), {'otp_code': otp.code})
        self.assertRedirects(response, reverse('dashboard'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.two_factor_enabled)

    def test_setup_2fa_verify_invalid_code(self):
        response = self.client.post(reverse('setup_2fa'), {'otp_code': '000000'})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.two_factor_enabled)

    def test_verify_2fa_redirects_when_2fa_disabled(self):
        response = self.client.get(reverse('verify_2fa'))
        self.assertRedirects(response, reverse('dashboard'))

    def test_verify_2fa_authenticated(self):
        self.user.two_factor_enabled = True
        self.user.save()
        response = self.client.get(reverse('verify_2fa'))
        self.assertEqual(response.status_code, 200)

    def test_verify_2fa_sends_otp_on_get(self):
        self.user.two_factor_enabled = True
        self.user.save()
        response = self.client.get(reverse('verify_2fa'))
        otp = EmailOTP.objects.filter(user=self.user, purpose='2fa', is_used=False).first()
        self.assertIsNotNone(otp)

    def test_verify_2fa_valid_code(self):
        self.user.two_factor_enabled = True
        self.user.save()
        self.client.get(reverse('verify_2fa'))
        otp = EmailOTP.objects.filter(user=self.user, purpose='2fa', is_used=False).first()
        CacheService.set_otp(self.user.id, otp.code)

        response = self.client.post(reverse('verify_2fa'), {'otp_code': otp.code})
        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(self.client.session.get('2fa_verified'))

    def test_toggle_2fa_disable(self):
        self.user.two_factor_enabled = True
        self.user.save()
        EmailOTP.objects.create(
            user=self.user, code='123456', purpose='2fa',
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = self.client.get(reverse('toggle_2fa'))
        self.assertRedirects(response, reverse('settings_profile'))
        self.user.refresh_from_db()
        self.assertFalse(self.user.two_factor_enabled)

    def test_resend_otp(self):
        self.user.two_factor_enabled = True
        self.user.save()
        self.client.get(reverse('verify_2fa'))

        response = self.client.post(reverse('resend_otp'))
        self.assertRedirects(response, reverse('verify_2fa'))

    def test_resend_otp_rate_limited(self):
        self.user.two_factor_enabled = True
        self.user.save()

        self.client.post(reverse('resend_otp'))
        self.client.post(reverse('resend_otp'))
        self.client.post(reverse('resend_otp'))

        response = self.client.post(reverse('resend_otp'), follow=True)
        messages = list(response.context['messages'])
        self.assertTrue(any('Too many requests' in str(m) for m in messages))


class APIViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='api@test.com', username='apiuser', password='SecurePass1!'
        )
        self.client.force_login(self.user)

    def test_dashboard_stats(self):
        response = self.client.get(reverse('api_dashboard_stats'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('balance', data)
        self.assertIn('open_trades', data)
        self.assertIn('win_rate', data)

    def test_market_data_endpoint(self):
        response = self.client.get(f'{reverse("api_market_data")}?symbol=EUR/USD')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('symbol', data)

    def test_execute_trade(self):
        response = self.client.post(reverse('api_execute_trade'), {
            'symbol': 'EUR/USD',
            'action': 'BUY',
            'volume': 0.1,
            'entry_price': 1.08000,
            'order_type': 'MARKET',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['symbol'], 'EUR/USD')
        self.assertEqual(data['status'], 'OPEN')

    def test_daily_trade_limit(self):
        self.user.daily_trades_count = 50
        self.user.last_trade_date = timezone.now().date()
        self.user.save()

        response = self.client.post(reverse('api_execute_trade'), {
            'symbol': 'EUR/USD',
            'action': 'BUY',
            'volume': 0.1,
            'entry_price': 1.08000,
            'order_type': 'MARKET',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 429)

    def test_create_signal(self):
        response = self.client.post(reverse('api_signal-list'), {
            'symbol': 'GBP/USD',
            'signal_type': 'BUY',
            'confidence': 80,
            'entry_price': 1.27000,
            'reasoning': 'Test signal',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201)

    def test_create_document(self):
        response = self.client.post(reverse('api_document-list'), {
            'title': 'Test Doc',
            'content': 'Test content for RAG. ' * 50,
            'source': 'manual',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data['is_indexed'])
        self.assertGreater(data['chunk_count'], 0)

    def test_request_otp_via_api(self):
        response = self.client.post(reverse('api_request_otp'), {
            'email': 'api@test.com',
            'purpose': '2fa',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)

    def test_verify_otp_via_api(self):
        otp = EmailOTP.objects.create(
            user=self.user, code='555555', purpose='2fa',
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        CacheService.set_otp(self.user.id, '555555')

        response = self.client.post(reverse('api_verify_otp'), {
            'email': 'api@test.com',
            'code': '555555',
            'purpose': '2fa',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'verified')

    def test_verify_otp_wrong_code(self):
        response = self.client.post(reverse('api_verify_otp'), {
            'email': 'api@test.com',
            'code': '000000',
            'purpose': '2fa',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_subscription_api(self):
        Subscription.objects.create(user=self.user, tier='FREE')
        response = self.client.get(reverse('api_subscription'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tier'], 'FREE')

    def test_broker_configuration(self):
        response = self.client.post(reverse('api_configure_broker'), {
            'api_key': 'test_key_123',
            'api_secret': 'test_secret_456',
            'account_id': 'acc_789',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.broker_api_key, 'test_key_123')

    def test_llm_query_no_api_key(self):
        response = self.client.post(reverse('api_llm_query'), {
            'query_type': 'market_analysis',
            'prompt': 'Analyze EUR/USD',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('response', data)

    def test_llm_query_rag_without_docs(self):
        response = self.client.post(reverse('api_llm_query'), {
            'query_type': 'rag_query',
            'prompt': 'What is a good trading strategy?',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)


class RateLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='ratelimit@test.com', username='ratelimit', password='SecurePass1!'
        )

    def test_login_rate_limit(self):
        for _ in range(5):
            self.client.post(reverse('login'), {
                'username': 'ratelimit@test.com',
                'password': 'WrongPass1!',
            })

        response = self.client.post(reverse('login'), {
            'username': 'ratelimit@test.com',
            'password': 'SecurePass1!',
        }, follow=True)
        messages_list = list(response.context['messages'])
        self.assertTrue(any('Too many login attempts' in str(m) for m in messages_list))


class BrokerServiceTests(TestCase):
    def test_not_configured(self):
        service = BrokerService(api_key='', endpoint='')
        self.assertFalse(service.is_configured())

    def test_configured(self):
        service = BrokerService(
            api_key='test_key',
            api_secret='test_secret',
            endpoint='https://api.test.com',
            account_id='acc_123',
        )
        self.assertTrue(service.is_configured())

    def test_not_configured_with_placeholder(self):
        service = BrokerService(
            api_key='your-trading-api-key',
            endpoint='https://api.test.com',
        )
        self.assertFalse(service.is_configured())

    def test_get_market_data_not_configured(self):
        service = BrokerService(api_key='', endpoint='')
        self.assertIsNone(service.get_market_data('EUR/USD'))

    def test_execute_trade_not_configured(self):
        service = BrokerService(api_key='', endpoint='')
        self.assertIsNone(service.execute_trade('EUR/USD', 'BUY', 0.1))


class AuthenticationEdgeCases(TestCase):
    def test_register_duplicate_email(self):
        User.objects.create_user(
            email='dup@test.com', username='dup1', password='Pass123!'
        )
        response = self.client.post(reverse('register'), {
            'email': 'dup@test.com',
            'username': 'dup2',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'broker': 'Deriv',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')

    def test_register_password_mismatch(self):
        response = self.client.post(reverse('register'), {
            'email': 'mismatch@test.com',
            'username': 'mismatch',
            'password1': 'StrongPass123!',
            'password2': 'DifferentPass1!',
            'broker': 'Deriv',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'password')

    def test_logout(self):
        self.client.force_login(
            User.objects.create_user(email='logout@test.com', username='logout', password='Pass123!')
        )
        response = self.client.get(reverse('logout'))
        self.assertRedirects(response, reverse('login'))

    def test_dashboard_redirects_when_not_logged_in(self):
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, f'{reverse("login")}?next={reverse("dashboard")}')


class TwoFactorDecoratorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='decorator@test.com', username='decorator', password='SecurePass1!'
        )

    def test_decorator_redirects_to_verify_2fa(self):
        self.user.two_factor_enabled = True
        self.user.save()
        self.client.force_login(self.user)

        # Calling dashboard should redirect to verify_2fa since 2fa is not verified in session
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, reverse('verify_2fa'))


class ProfileViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='profile@test.com', username='profile', password='SecurePass1!', broker='XM'
        )
        self.client.force_login(self.user)

    def test_update_email(self):
        response = self.client.post(reverse('settings_profile'), {
            'email': 'updated@test.com',
            'broker': 'Deriv',
        })
        self.assertRedirects(response, reverse('settings_profile'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'updated@test.com')

    def test_update_broker(self):
        response = self.client.post(reverse('settings_profile'), {
            'email': 'profile@test.com',
            'broker': 'IC Markets',
        })
        self.user.refresh_from_db()
        self.assertEqual(self.user.broker, 'IC Markets')


class PasswordResetViewTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = User.objects.create_user(
            email='reset@test.com', username='resetuser', password='OldPass123!'
        )

    def test_password_reset_page_loads(self):
        response = self.client.get(reverse('password_reset_request'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/password_reset_request.html')

    def test_password_reset_request_valid_email(self):
        response = self.client.post(reverse('password_reset_request'), {
            'email': 'reset@test.com',
        })
        self.assertRedirects(response, reverse('password_reset_verify'))
        otps = EmailOTP.objects.filter(user=self.user, purpose='password_reset')
        self.assertEqual(otps.count(), 1)
        self.assertEqual(len(otps.first().code), 6)

    def test_password_reset_request_invalid_email(self):
        response = self.client.post(reverse('password_reset_request'), {
            'email': 'nonexistent@test.com',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No account found')

    def test_password_reset_verify_valid_code(self):
        self.client.post(reverse('password_reset_request'), {'email': 'reset@test.com'})
        otp = EmailOTP.objects.filter(user=self.user, purpose='password_reset').first()
        CacheService.set_otp(self.user.id, otp.code)

        response = self.client.post(reverse('password_reset_verify'), {
            'otp_code': otp.code,
        })
        self.assertRedirects(response, reverse('password_reset_confirm'))

    def test_password_reset_verify_invalid_code(self):
        self.client.post(reverse('password_reset_request'), {'email': 'reset@test.com'})
        response = self.client.post(reverse('password_reset_verify'), {
            'otp_code': '000000',
        })
        self.assertEqual(response.status_code, 200)

    def test_password_reset_verify_without_session(self):
        response = self.client.get(reverse('password_reset_verify'))
        self.assertRedirects(response, reverse('password_reset_request'))

    def test_password_reset_confirm_valid(self):
        self.client.post(reverse('password_reset_request'), {'email': 'reset@test.com'})
        otp = EmailOTP.objects.filter(user=self.user, purpose='password_reset').first()
        CacheService.set_otp(self.user.id, otp.code)
        self.client.post(reverse('password_reset_verify'), {'otp_code': otp.code})

        response = self.client.post(reverse('password_reset_confirm'), {
            'new_password1': 'NewStrongPass456!',
            'new_password2': 'NewStrongPass456!',
        })
        self.assertRedirects(response, reverse('login'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewStrongPass456!'))
        self.assertFalse(self.user.check_password('OldPass123!'))

    def test_password_reset_confirm_without_session(self):
        response = self.client.get(reverse('password_reset_confirm'))
        self.assertRedirects(response, reverse('password_reset_request'))

    def test_password_reset_redirects_when_authenticated(self):
        self.client.force_login(self.user)
        for url_name in ['password_reset_request', 'password_reset_verify', 'password_reset_confirm']:
            response = self.client.get(reverse(url_name))
            self.assertRedirects(response, reverse('dashboard'))

    def test_resend_reset_otp(self):
        self.client.post(reverse('password_reset_request'), {'email': 'reset@test.com'})
        response = self.client.post(reverse('resend_reset_otp'))
        self.assertRedirects(response, reverse('password_reset_verify'))

    def test_resend_reset_otp_without_session(self):
        response = self.client.post(reverse('resend_reset_otp'))
        self.assertRedirects(response, reverse('password_reset_request'))

    def test_full_reset_flow(self):
        response = self.client.get(reverse('password_reset_request'))
        self.assertEqual(response.status_code, 200)

        self.client.post(reverse('password_reset_request'), {'email': 'reset@test.com'})
        otp = EmailOTP.objects.filter(user=self.user, purpose='password_reset').first()
        CacheService.set_otp(self.user.id, otp.code)

        self.client.post(reverse('password_reset_verify'), {'otp_code': otp.code})
        self.client.post(reverse('password_reset_confirm'), {
            'new_password1': 'FinalNewPass789!',
            'new_password2': 'FinalNewPass789!',
        })

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('FinalNewPass789!'))

    @override_settings(MAX_LOGIN_ATTEMPTS=10)
    def test_forgot_password_link_on_login(self):
        response = self.client.get(reverse('login'))
        self.assertContains(response, reverse('password_reset_request'))

    def test_password_reset_rate_limit(self):
        for _ in range(3):
            self.client.post(reverse('password_reset_request'), {'email': 'reset@test.com'})
        response = self.client.post(reverse('password_reset_request'), {'email': 'reset@test.com'}, follow=True)
        messages_list = list(response.context['messages'])
        self.assertTrue(any('Too many password reset' in str(m) for m in messages_list))


class PasswordResetAPIViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='apireset@test.com', username='apiresetuser', password='OldPass123!'
        )

    def test_api_password_reset_request(self):
        response = self.client.post(reverse('api_password_reset_request'), {
            'email': 'apireset@test.com',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'OTP sent')

    def test_api_password_reset_request_missing_email(self):
        response = self.client.post(reverse('api_password_reset_request'), {},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_api_password_reset_request_invalid_email(self):
        response = self.client.post(reverse('api_password_reset_request'), {
            'email': 'nobody@test.com',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 404)

    def test_api_password_reset_verify_valid(self):
        self.client.post(reverse('api_password_reset_request'), {
            'email': 'apireset@test.com',
        }, content_type='application/json')
        otp = EmailOTP.objects.filter(user=self.user, purpose='password_reset').first()
        CacheService.set_otp(self.user.id, otp.code)

        response = self.client.post(reverse('api_password_reset_verify'), {
            'email': 'apireset@test.com',
            'code': otp.code,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'verified')

    def test_api_password_reset_verify_invalid(self):
        response = self.client.post(reverse('api_password_reset_verify'), {
            'email': 'apireset@test.com',
            'code': '000000',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_api_password_reset_confirm(self):
        self.client.post(reverse('api_password_reset_request'), {
            'email': 'apireset@test.com',
        }, content_type='application/json')
        otp = EmailOTP.objects.filter(user=self.user, purpose='password_reset').first()
        CacheService.set_otp(self.user.id, otp.code)
        self.client.post(reverse('api_password_reset_verify'), {
            'email': 'apireset@test.com',
            'code': otp.code,
        }, content_type='application/json')

        response = self.client.post(reverse('api_password_reset_confirm'), {
            'email': 'apireset@test.com',
            'new_password': 'ApiNewPass456!',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('ApiNewPass456!'))

    def test_api_password_reset_confirm_short_password(self):
        response = self.client.post(reverse('api_password_reset_confirm'), {
            'email': 'apireset@test.com',
            'new_password': '123',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400)


class SecurityQuestionModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='secq@test.com', username='secquser', password='Pass123!'
        )

    def test_create_security_question(self):
        q = SecurityQuestion.objects.create(
            user=self.user,
            question_key='pet',
            answer_hash=str(hash('fluffy')),
        )
        self.assertEqual(q.question_key, 'pet')
        self.assertEqual(q.get_question_key_display(), "What was the name of your first pet?")

    def test_security_question_unique_together(self):
        SecurityQuestion.objects.create(
            user=self.user, question_key='city', answer_hash='hash1'
        )
        with self.assertRaises(Exception):
            SecurityQuestion.objects.create(
                user=self.user, question_key='city', answer_hash='hash2'
            )

    def test_security_question_str(self):
        q = SecurityQuestion.objects.create(
            user=self.user, question_key='school',
            answer_hash=str(hash('sunnydale')),
        )
        self.assertIn('secq@test.com', str(q))
        self.assertIn('elementary', str(q))


class SecurityQuestionViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='secqview@test.com', username='secqview', password='Pass123!'
        )
        self.client.force_login(self.user)

    def test_setup_page_loads(self):
        response = self.client.get(reverse('setup_security_questions'))
        self.assertEqual(response.status_code, 200)

    def test_add_security_question(self):
        response = self.client.post(reverse('setup_security_questions'), {
            'question_key': 'pet',
            'answer': 'Fluffy',
        })
        self.assertRedirects(response, reverse('setup_security_questions'))
        self.assertEqual(SecurityQuestion.objects.filter(user=self.user).count(), 1)

    def test_delete_security_question(self):
        SecurityQuestion.objects.create(
            user=self.user, question_key='city', answer_hash='hash'
        )
        response = self.client.get(reverse('delete_security_question', args=['city']))
        self.assertRedirects(response, reverse('setup_security_questions'))
        self.assertEqual(SecurityQuestion.objects.filter(user=self.user).count(), 0)


class EmailValidationTests(TestCase):
    def test_disposable_email_rejected(self):
        form = RegisterForm(data={
            'email': 'test@mailinator.com',
            'username': 'testuser1',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'broker': 'Deriv',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('Disposable email', str(form.errors.get('email', '')))

    def test_common_fake_email_rejected(self):
        form = RegisterForm(data={
            'email': 'test@test.com',
            'username': 'testuser2',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'broker': 'Deriv',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('real email', str(form.errors.get('email', '')))

    def test_valid_email_accepted(self):
        form = RegisterForm(data={
            'email': 'realperson@gmail.com',
            'username': 'testuser3',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'broker': 'Deriv',
        })
        user_count_before = User.objects.count()
        # Form might fail on other validations (password etc), just check email
        email_errors = form.errors.get('email', [])
        self.assertEqual(len(email_errors), 0)

    def test_profile_form_email_validation(self):
        user = User.objects.create_user(
            email='profilevalid@test.com', username='profval', password='Pass123!'
        )
        form = ProfileForm(data={'email': 'test@mailinator.com'}, instance=user)
        self.assertFalse(form.is_valid())
        self.assertIn('Disposable email', str(form.errors.get('email', '')))


class ProfileViewTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = User.objects.create_user(
            email='profileview@test.com', username='profview', password='Pass123!',
            broker='Deriv',
        )
        self.client.force_login(self.user)

    def test_profile_page_loads(self):
        response = self.client.get(reverse('profile_page'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/profile.html')

    def test_profile_update_email(self):
        response = self.client.post(reverse('profile_page'), {
            'update_profile': '1',
            'email': 'updatedprofile@test.com',
            'broker': 'IC Markets',
            'phone': '+1234567890',
            'bio': 'A crypto trader',
        })
        self.assertRedirects(response, reverse('profile_page'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'updatedprofile@test.com')
        self.assertEqual(self.user.broker, 'IC Markets')
        self.assertEqual(self.user.phone, '+1234567890')
        self.assertEqual(self.user.bio, 'A crypto trader')

    def test_profile_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('profile_page'))
        self.assertRedirects(response, f'{reverse("login")}?next={reverse("profile_page")}')

    def test_settings_profile_update(self):
        response = self.client.post(reverse('settings_profile'), {
            'update_profile': '1',
            'email': 'settingsupdate@test.com',
            'broker': 'Exness',
        })
        self.assertRedirects(response, reverse('settings_profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'settingsupdate@test.com')

    def test_avatar_upload_and_remove(self):
        import tempfile, os
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io

        # Create a small test image
        img = Image.new('RGB', (100, 100), color='red')
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        buf.seek(0)

        response = self.client.post(reverse('profile_page'), {
            'update_avatar': '1',
            'avatar': SimpleUploadedFile('test.jpg', buf.getvalue(), content_type='image/jpeg'),
        })
        self.assertRedirects(response, reverse('profile_page'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)

        # Remove avatar
        response = self.client.post(reverse('profile_page'), {
            'remove_avatar': '1',
        })
        self.assertRedirects(response, reverse('profile_page'))
        self.user.refresh_from_db()
        self.assertFalse(self.user.avatar)

    def test_avatar_displayed_in_sidebar(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io
        img = Image.new('RGB', (50, 50), color='blue')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        self.client.post(reverse('profile_page'), {
            'update_avatar': '1',
            'avatar': SimpleUploadedFile('avatar.png', buf.getvalue(), content_type='image/png'),
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'sidebar-avatar')
        self.assertContains(response, '/media/avatars/')

    def test_broker_config_page(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
