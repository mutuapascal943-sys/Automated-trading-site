# API / Integration Register

This document registers all external APIs, data providers, broker integrations, payment gateways, authentication services, and notification services used by Forex AI Pro.

Credentials are configured exclusively via the git-ignored `.env` file. No credentials are hardcoded or printed to logs.

---

## Registered Integrations Summary

| Provider / API Name | Environment Variable(s) | Source File / Module | Feature / Function | Credentials Source | Mandatory / Optional | Connection Test Procedure | Missing/Invalid Credential Behavior |
|---|---|---|---|---|---|---|---|
| **Django Framework Core** | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `SECURE_SSL_REDIRECT`, `ADMIN_EMAIL` | `forex_ai_pro/settings.py`, `trading_app/services/admin_access.py` | Web framework, session management, CSRF protection, RBAC admin authorization | Environment / Operator | **Mandatory** (`DJANGO_SECRET_KEY`) | `python manage.py check` | Startup fails immediately if `DJANGO_SECRET_KEY` is empty. Default fallback to `manesapascal@gmail.com` for `ADMIN_EMAIL`. |
| **Database (SQLite / PostgreSQL)** | `DATABASE_URL` | `forex_ai_pro/settings.py` | Relational persistence for Users, Signals, Trades, Payments, Audit Logs | Local file or Managed DB host | Optional (defaults to SQLite `db.sqlite3`) | `python manage.py check` | Falls back to local SQLite database when unset. |
| **Redis Cache / Channel Layer** | `REDIS_URL`, `CACHE_URL` | `forex_ai_pro/settings.py` | Distributed caching, WebSocket Channels fan-out, rate-limiting | Local or Cloud Redis instance | Optional | Internal health probe / Ping during startup | Falls back to in-memory channel layer and local-memory cache if unreachable or unset. |
| **Celery Task Broker** | `CELERY_BROKER_URL` | `forex_ai_pro/celery.py`, `trading_app/tasks.py` | Asynchronous background processing (bot cycles, prediction resolution) | Redis / RabbitMQ instance | Optional | Celery worker ping / auto-reachability test | System executes tasks synchronously in-process (`CELERY_TASK_ALWAYS_EAGER=True`) if unreachable. |
| **Deriv WebSocket API** | `DERIV_APP_ID` | `trading_app/trading_bot/deriv_adapter.py`, `trading_app/services/adapter_resolver.py`, `trading_app/services/ticker_bridge.py`, `trading_app/ml/data_collector.py` | Public live market data feeds, ticks, historical candles (no auth token needed) | https://api.deriv.com dashboard | Optional (Public feed works without app ID) | `python manage.py test trading_app.tests.test_deriv_public_feed` | Uses default public app ID if omitted. If WebSocket connection fails, API returns HTTP 503 Service Unavailable / WAIT. No fake candles are generated. |
| **Safaricom Daraja (M-Pesa STK Push)** | `DARAJA_ENVIRONMENT`, `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_SHORTCODE`, `DARAJA_PASSKEY`, `DARAJA_CALLBACK_URL`, `DARAJA_BASIC_AMOUNT_KES`, `DARAJA_TRANSACTION_TYPE` | `trading_app/payments/daraja.py`, `trading_app/payments/callback.py` | M-Pesa STK Push payment processing for subscription checkouts | Safaricom Developer Portal (https://developer.safaricom.co.ke) | **Mandatory for payment feature** | `python manage.py test trading_app.tests.test_daraja_payments` | Checkout endpoint returns HTTP 400 Bad Request listing missing variables. Payments are blocked until configured; no dummy subscriptions are granted. |
| **SMTP Email Service** | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `EMAIL_FILE_BACKEND` | `trading_app/email_config.py`, `trading_app/services/email_diagnostics.py` | Sending OTP verification codes, 2FA codes, password reset links | SMTP Provider (Gmail, SendGrid, Mailgun, etc.) | **Mandatory for production 2FA/Reset** | `python manage.py check_email` | Diagnostics reports `ready: False`. In DEBUG mode, outputs emails to console/file. In production, returns HTTP 500 on delivery failure with sanitized user message. |
| **Legacy Generic Broker REST Service** | `TRADING_API_KEY`, `TRADING_API_SECRET`, `TRADING_API_ENDPOINT`, `TRADING_ACCOUNT_ID` | `trading_app/services/broker_service.py` | Legacy execution client | External Broker REST API | Optional (Execution disabled) | `BrokerService().is_configured()` check | Service reports unconfigured. Trade execution endpoints return HTTP 410 Gone as platform is read-only. |

---

## Detailed Provider Specifications

### 1. Django Core & Admin RBAC
- **Provider**: Internal Django Framework
- **Required Variables**: `DJANGO_SECRET_KEY`, `ADMIN_EMAIL`
- **Consuming File**: `forex_ai_pro/settings.py`, `trading_app/services/admin_access.py`
- **Purpose**: System security, session signing, password hashing, admin identification.
- **Obtain Credentials**: Generate secret key via `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`. Set `ADMIN_EMAIL` to the authorized owner's email address.
- **Testing Connection**: Run `python manage.py check`.
- **Failure Behavior**: Process crashes immediately if `DJANGO_SECRET_KEY` is missing.

### 2. Deriv Public Market Data
- **Provider**: Deriv.com WebSocket API (`wss://ws.derivws.com/websockets/v3`)
- **Required Variables**: `DERIV_APP_ID` (Optional, defaults to default public app ID)
- **Consuming Files**: `trading_app/trading_bot/deriv_adapter.py`, `trading_app/services/ticker_bridge.py`, `trading_app/ml/data_collector.py`
- **Purpose**: Real-time tick stream and multi-timeframe OHLCV historical candle retrieval for Volatility Indices and Forex pairs.
- **Obtain Credentials**: Register an app at https://api.deriv.com to obtain a custom `DERIV_APP_ID`. Note: **No API token is required or allowed** for public market data.
- **Testing Connection**: `python manage.py test trading_app.tests.test_deriv_public_feed`
- **Failure Behavior**: If Deriv servers are unreachable or network drops, analysis returns explicit `"WAIT"` decision with reason `"Live market data unavailable."` No synthetic candles or prices are fabricated.

### 3. Safaricom Daraja M-Pesa STK Push
- **Provider**: Safaricom Daraja API
- **Required Variables**: `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_SHORTCODE`, `DARAJA_PASSKEY`, `DARAJA_CALLBACK_URL`, `DARAJA_BASIC_AMOUNT_KES`
- **Consuming Files**: `trading_app/payments/daraja.py`, `trading_app/payments/callback.py`, `trading_app/api_views.py`
- **Purpose**: Mobile payment processing for subscription activation.
- **Obtain Credentials**: Create an app in Safaricom Developer Portal (https://developer.safaricom.co.ke).
- **Testing Connection**: `python manage.py test trading_app.tests.test_daraja_payments`
- **Failure Behavior**: `DarajaConfig.validate()` raises an error detailing missing environment variables. The API returns an explicit error to the client. Unconfirmed payments remain pending or timed out without granting access.

### 4. SMTP Email Service
- **Provider**: SMTP Email Server
- **Required Variables**: `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`
- **Consuming Files**: `trading_app/email_config.py`, `trading_app/services/email_service.py`, `trading_app/services/email_diagnostics.py`
- **Purpose**: 2FA OTP codes, registration verification, password reset tokens.
- **Obtain Credentials**: SMTP provider settings (e.g. Gmail App Password, SendGrid API key).
- **Testing Connection**: Execute `python manage.py check_email`.
- **Failure Behavior**: Diagnostic check flags `ready: False`. System logs error and reports delivery failure.
