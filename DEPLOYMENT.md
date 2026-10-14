# Deployment Preparation

This project is analysis-only: order execution APIs return 410, while market analysis uses live Deriv data and returns WAIT when required evidence is unavailable. Never enable simulated market data as a production fallback.

## Runtime

- Python: use the project-supported Python version and install `requirements.txt`.
- Database: use PostgreSQL for multi-worker staging/production. Set `DATABASE_URL` to a private PostgreSQL URL; URL-encoded username/password are supported. SQLite is for local development only.
- ASGI: serve `forex_ai_pro.asgi:application` with Daphne or an ASGI-capable process manager. WebSocket market updates require ASGI and a Channels layer.
- Redis: configure `REDIS_URL`, `CACHE_URL`, and `CELERY_BROKER_URL` to the private Redis service for shared cache, Channels, and distributed jobs. Without Redis, development uses process-local cache and the in-process scheduler; do not rely on that fallback for horizontally scaled production.
- Background work: run one scheduler/worker instance only. The in-process scheduler registers a 5-second expired-WAIT check and the periodic outcome/model jobs when broker infrastructure is unavailable. With Celery infrastructure, configure a single Celery Beat instance and workers; do not run both schedulers concurrently.
- Static files: run `python manage.py collectstatic --noinput`, serve `STATIC_ROOT` through the chosen web server/CDN, and configure media storage separately. Do not rely on Django's development static/media serving in production.

## Secure Environment and Initialization

1. Configure your private `.env` in the project root or set the equivalent environment variables in the deployment secret manager.
2. Generate a unique `DJANGO_SECRET_KEY` locally/within the secret manager. Never put it in source control or logs.
3. Set `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, and `SECURE_SSL_REDIRECT=True` for an HTTPS deployment. Use exact origins, including scheme; avoid wildcard credentialed CORS.
4. Set `DATABASE_URL` and infrastructure URLs, then run `python manage.py migrate --noinput` and `python manage.py check --deploy`.
5. Provision the authorized owner via the `setup_owner_admin` management command in a secure interactive session. Do not put the owner password in deployment manifests.
6. Configure HTTPS termination and forward the original HTTPS scheme so Django's `SECURE_PROXY_SSL_HEADER` is accurate. Confirm secure cookies and CSRF behavior through the actual proxy before opening access.
7. Configure an SMTP provider and Daraja callback URL reachable over public HTTPS, then test those integrations in their provider-approved sandbox before production.

## Deriv Feed

The application uses Deriv's public read-only WebSocket endpoints for ticks and candles. Set `DERIV_APP_ID` to the registered application ID if the default is unsuitable. Do not add account trading/order permissions. The application fails closed to WAIT/unavailable when fresh symbol-matched timeframe data or a current tick cannot be verified. No real Deriv account secret is required by the analysis feed.

## Daraja Callback and SMTP

The callback endpoint is `/api/payments/daraja/callback/`. It must resolve publicly to this application over HTTPS in production and be registered with Safaricom. A callback is not itself sufficient for subscription activation: the server performs provider status query and validates callback metadata. Use only provider-issued environment values. SMTP account verification, sender/domain setup, and any app-password authorization are handled in the mail provider dashboard.

## Track B — MANUAL USER ACTION REQUIRED

- **Django hosting / DNS / HTTPS**: In the selected hosting and DNS provider dashboards, create the web service and domain, provision a TLS certificate, and obtain the canonical HTTPS origin and hostnames. Set `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, and `SECURE_SSL_REDIRECT`. Required for staging and production; no application secret is involved.
- **Database provider**: Create a private PostgreSQL database in the hosting provider dashboard and place its connection URL in `DATABASE_URL` in that provider's secret store. Required for production and recommended for shared staging; secrets must remain outside the repository.
- **Redis/Celery provider**: Create private Redis access and configure `REDIS_URL`, `CACHE_URL`, and `CELERY_BROKER_URL`. Required for multi-worker production/shared staging. Choose exactly one scheduler owner; no real-world transaction is required.
- **Deriv application registration**: In the Deriv developer dashboard, register/select an application and obtain its application ID for `DERIV_APP_ID`. This can be tested with public read-only feed requests without money or order permissions; provider account access is required to register it.
- **Safaricom Daraja**: In the Safaricom Daraja developer/business portal, obtain the environment-specific consumer key, consumer secret, shortcode, passkey, and approved transaction type; set `DARAJA_ENVIRONMENT`, `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_SHORTCODE`, `DARAJA_PASSKEY`, `DARAJA_CALLBACK_URL`, `DARAJA_BASIC_AMOUNT_KES`, and `DARAJA_TRANSACTION_TYPE` in the secret manager. Sandbox testing can use provider test credentials and a reachable HTTPS callback; no production money should be used for staging. Production approval and callback registration are provider-required.
- **SMTP provider**: In the chosen email provider dashboard, verify a sender/domain and obtain SMTP host/port/username/password; set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL` in secrets. Required for real OTP delivery in staging/production; provider-specific verification/authorization may be required.
- **Owner account provisioning**: Once deployment secrets/database are ready, run `python manage.py setup_owner_admin` from a secure session and enter the password directly into its hidden prompt. Required per deployed database; no password should be sent through chat or committed.
