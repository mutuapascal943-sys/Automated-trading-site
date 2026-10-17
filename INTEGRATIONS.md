# API / Integration Register

This document registers all external APIs, data providers, broker integrations, payment methods, authentication services, and notification services used by Forex AI Pro.

Credentials are configured exclusively via the git-ignored `.env` file. No credentials are hardcoded or printed to logs.

---

## Registered Integrations Summary

| Provider / API Name | Environment Variable(s) | Source File / Module | Feature / Function | Credentials Source | Mandatory / Optional | Connection Test Procedure | Missing/Invalid Credential Behavior |
|---|---|---|---|---|---|---|---|
| **Django Framework Core** | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `SECURE_SSL_REDIRECT`, `ADMIN_EMAIL` | `forex_ai_pro/settings.py`, `trading_app/services/admin_access.py` | Web framework, session management, CSRF protection, RBAC admin authorization | Environment / Operator | **Mandatory** (`DJANGO_SECRET_KEY`) | `python manage.py check` | Startup fails immediately if `DJANGO_SECRET_KEY` is empty. Default fallback to `manesapascal@gmail.com` for `ADMIN_EMAIL`. |
| **Database (SQLite / PostgreSQL)** | `DATABASE_URL` | `forex_ai_pro/settings.py` | Relational persistence for Users, Signals, Trades, Payments, Audit Logs | Local file or Managed DB host | Optional (defaults to SQLite `db.sqlite3`) | `python manage.py check` | Falls back to local SQLite database when unset. |
| **Redis Cache / Channel Layer** | `REDIS_URL`, `CACHE_URL` | `forex_ai_pro/settings.py` | Distributed caching, WebSocket Channels fan-out, rate-limiting | Local or Cloud Redis instance | Optional | Internal health probe / Ping during startup | Falls back to in-memory channel layer and local-memory cache if unreachable or unset. |
| **Celery Task Broker** | `CELERY_BROKER_URL` | `forex_ai_pro/celery.py`, `trading_app/tasks.py` | Asynchronous background processing (bot cycles, prediction resolution) | Redis / RabbitMQ instance | Optional | Celery worker ping / auto-reachability test | System executes tasks synchronously in-process (`CELERY_TASK_ALWAYS_EAGER=True`) if unreachable. |
| **Deriv WebSocket API** | `DERIV_APP_ID`, `DERIV_WS_PUBLIC_URL` | `trading_app/trading_bot/deriv_adapter.py`, `trading_app/services/adapter_resolver.py`, `trading_app/services/ticker_bridge.py`, `trading_app/ml/data_collector.py` | Public live market data feeds, ticks, historical candles (no auth token needed) | https://api.deriv.com dashboard | Optional (Public feed works without app ID) | `python manage.py test trading_app.tests.test_deriv_public_feed` | Uses default public app ID if omitted. If WebSocket connection fails, API returns HTTP 503 Service Unavailable / WAIT. No fake candles are generated. |
| **Manual M-Pesa Send Money** | `DARAJA_BASIC_AMOUNT_KES` | `trading_app/api_views.py`, `trading_app/admin.py` | Manual M-Pesa subscription payments to recipient **0705794964** with admin verification | Manual M-Pesa transaction reference | Optional | `python manage.py test` | Subscription checkout stores transaction as `PENDING` until administrator verification in Django admin. |
| **SMTP Email Service** | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `EMAIL_FILE_BACKEND` | `trading_app/email_config.py`, `trading_app/services/email_service.py`, `trading_app/services/email_diagnostics.py` | Sending OTP verification codes, 2FA codes, password reset links | SMTP Provider (Gmail, SendGrid, Mailgun, etc.) | **Mandatory for production 2FA/Reset** | `python manage.py check_email` | Diagnostics reports `ready: False`. In DEBUG mode, outputs emails to console/file. In production, returns HTTP 500 on delivery failure with sanitized user message. |
| **Legacy Generic Broker REST Service** | `TRADING_API_KEY`, `TRADING_API_SECRET`, `TRADING_API_ENDPOINT`, `TRADING_ACCOUNT_ID` | `trading_app/services/broker_service.py` | Legacy execution client | External Broker REST API | Optional (Execution disabled) | `BrokerService().is_configured()` check | Service reports unconfigured. Trade execution endpoints return HTTP 410 Gone as platform is read-only. |

---

## Detailed Provider Specifications

### 1. Django Core & Admin RBAC
- **Provider**: Internal Django Framework
- **Required Variables**: `DJANGO_SECRET_KEY`, `ADMIN_EMAIL`
- **Consuming File**: `forex_ai_pro/settings.py`, `trading_app/services/admin_access.py`
- **Purpose**: System security, session signing, password hashing, admin identification.
- **Testing Connection**: Run `python manage.py check`.

### 2. Deriv Public Market Data
- **Provider**: Deriv.com WebSocket API (`wss://api.derivws.com/trading/v1/options/ws/public`)
- **Required Variables**: `DERIV_APP_ID`, `DERIV_WS_PUBLIC_URL`
- **Consuming Files**: `trading_app/trading_bot/deriv_adapter.py`, `trading_app/services/ticker_bridge.py`, `trading_app/ml/data_collector.py`
- **Purpose**: Real-time tick stream and multi-timeframe OHLCV historical candle retrieval for Volatility Indices and Forex pairs.
- **Testing Connection**: `python manage.py test trading_app.tests.test_deriv_public_feed`
- **Failure Behavior**: If Deriv servers are unreachable or network drops, analysis returns explicit `"WAIT"` decision with reason `"Live market data unavailable."` No synthetic candles or prices are fabricated.

### 3. Manual M-Pesa Send Money
- **Provider**: Manual M-Pesa Transfer to **0705794964**
- **Required Variables**: `DARAJA_BASIC_AMOUNT_KES`
- **Consuming Files**: `trading_app/api_views.py`, `trading_app/admin.py`, `templates/dashboard/subscription_panel.html`
- **Purpose**: Manual subscription payment verification workflow.
- **Testing Connection**: Automated tests in `test_subscription_access.py`.
- **Failure Behavior**: Submissions are stored as `PENDING` until approved by an authorized administrator.

### 4. SMTP Email Service
- **Provider**: SMTP Email Server
- **Required Variables**: `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`
- **Consuming Files**: `trading_app/email_config.py`, `trading_app/services/email_service.py`, `trading_app/services/email_diagnostics.py`
- **Purpose**: 2FA OTP codes, registration verification, password reset tokens.
- **Testing Connection**: Execute `python manage.py check_email`.
