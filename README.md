# Forex AI Pro

Django/DRF market-analysis platform with a read-only Deriv feed, multi-strategy confluence, server-side subscription and bot access, administrator management, and Daraja STK Push subscriptions. The production analysis decision path fails closed to WAIT when live data or required evidence is missing. Order execution routes are disabled.

## Market Data and Decisions

Production analysis obtains an uncached current Deriv tick and symbol-matched candles for H4, M15, M5, and M1. Each timeframe is timestamp-checked. Missing, stale, future-dated, or mismatched feed data produces WAIT; no simulated price or candle is substituted in the decision path. Momentum, Support/Resistance, Fibonacci, ICT, CHoCH, and Technical/SMC evidence pass through centralized confluence; contradiction or insufficient required evidence produces WAIT. ML can veto an aligned decision but cannot create one.

Paper broker and deterministic simulated-market modules remain for isolated legacy paper/backtest utilities and tests. They are not valid production market-analysis inputs. The application does not place or modify live/paper orders.

## Local Development

1. Configure your private `.env` file in the root directory; keep real credentials out of source control.
2. Install `requirements.txt` in the project virtual environment.
3. Run `python manage.py migrate`.
4. Run `python manage.py setup_owner_admin` from a secure interactive terminal to provision the owner account.
5. Start the ASGI application using [DEPLOYMENT.md](DEPLOYMENT.md).

## Payments and Verification

Subscription checkout uses Daraja STK Push. A transaction remains pending until the callback is correlated to its payment and the provider status query and amount, phone, and receipt checks succeed. Local automated tests use isolated provider mocks; they do not represent a real transaction. OTPs are hashed, purpose-bound, expiring, and attempt-limited. A real SMTP provider is required for real email delivery.

The internal analysis verification session is available only when DEBUG and `TRADING_ANALYSIS_VERIFICATION_ENABLED` are explicitly enabled and the request is loopback-only. Never expose those routes publicly.

## Verification

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check
.\.venv\Scripts\python.exe manage.py test
```

If running tests in groups, include `trading_app.test_legacy`, `trading_app.tests_trading_bot`, and every module under `trading_app/tests/`.

See [DEPLOYMENT.md](DEPLOYMENT.md) for production setup, HTTPS, PostgreSQL, ASGI, Redis/Celery, static assets, and external-provider actions.
