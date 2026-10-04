# Market Analysis and Signal Platform

Django application for market charts, candle data, strategy analysis, saved signals, signal history, and analytics. The system does not execute live or simulated orders. Existing adapter implementations may remain in the repository for reference, but application routes and scheduled jobs do not place, modify, or close trades.

## Market Data Flow

- The active live-data provider is Deriv, accessed read-only for historical candles and WebSocket ticks.
- `MARKET_DATA_API_TOKEN` is the optional Deriv authorization token for market-data requests. Add it to `.env` after obtaining a token with market-data access. Do not send or store order permissions for this analysis-only application.
- `DERIV_APP_ID` is the Deriv application ID; the current code defaults to `1089`. Use your own registered ID if required by your Deriv setup.
- With no token, the app can use public Deriv candle/tick requests where supported. If Deriv is unreachable or a symbol cannot be served, charts use the built-in deterministic simulated market. Simulated values are for development and are not live prices.
- The market UI, chart-candle REST endpoint, WebSocket stream, strategy analyzer, SMC rules, and ML inference operate on read-only OHLC/tick data.
- Historical model training uses the same Deriv data connection and defaults to `MARKET_DATA_API_TOKEN` when a token is needed.

Example `.env` entries (values intentionally omitted):

```dotenv
MARKET_DATA_API_TOKEN=
DERIV_APP_ID=1089
```

## Disabled Operations

Trade creation, trade modification, broker configuration/health checks, order backtesting, paper orders, and live orders are disabled. Legacy execution endpoints return an explicit `410 Gone` response. The application does not need Binance, MetaTrader, or broker-account/order credentials for the specified analysis-only features.

## Local Verification Session

For a local live-analysis verification only, the development session issuer is available when both `DJANGO_DEBUG=True` and `TRADING_ANALYSIS_VERIFICATION_ENABLED=True`. It additionally requires a loopback host and client address (`127.0.0.1`, `localhost`, or `::1`). The normal analysis-verification endpoint remains authenticated and staff-only; no production authorization is bypassed.

The development session is temporary, expires after 10 minutes, and can call only `/api/internal/analysis-verification/` and its cleanup endpoint. It creates an unusable-password, non-superuser staff identity with an isolated `dev-analysis-verification-*` username; it does not reuse or alter another user. Always clean it up after use. If interrupted, remove any leftover `dev-analysis-verification-*` identity from the local development database through Django admin or the Django shell.

With the local Django server running on `127.0.0.1:8000`, use one PowerShell web session for the CSRF cookie and the temporary login:

```powershell
$base = 'http://127.0.0.1:8000'
$path = '/api/internal/dev/verification-session/'
$bootstrapResponse = Invoke-WebRequest -Uri ($base + $path) -SessionVariable session
$bootstrap = $bootstrapResponse.Content | ConvertFrom-Json
$csrfHeaders = @{ 'X-CSRFToken' = $bootstrap.csrf_token }
$issuedResponse = Invoke-WebRequest -Method Post -Uri ($base + $path) -WebSession $session -Headers $csrfHeaders -ContentType 'application/json' -Body '{}'
$issued = $issuedResponse.Content | ConvertFrom-Json
$csrfHeaders = @{ 'X-CSRFToken' = $issued.csrf_token }
$analysis = Invoke-RestMethod -Method Post -Uri ($base + '/api/internal/analysis-verification/') -WebSession $session -Headers $csrfHeaders -ContentType 'application/json' -Body '{"symbol":"R_75","granularity":300,"persist":false}'
$cleanup = Invoke-RestMethod -Method Post -Uri ($base + '/api/internal/dev/verification-session/cleanup/') -WebSession $session -Headers $csrfHeaders -ContentType 'application/json' -Body '{}'
```

The bootstrap GET creates no user. The issuer's POST returns a refreshed CSRF token because Django rotates it on login. The temporary identity uses the normal trial-access check, has no password, is not a superuser, and expires with the session; cleanup deletes that identity explicitly. The session issuer itself does not perform analysis. Do not expose these routes through a public tunnel or reverse proxy.

## Other External Services

- Email service: optional for real OTP delivery; configure `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and sender settings. Local development can use Django's file or in-memory email backend.
- Redis/Celery: optional infrastructure for shared cache and distributed background tasks. The project has local-memory cache and in-process scheduler fallbacks.
- Payment provider: not integrated. Subscription checkout only supports the explicitly enabled development mock; production subscriptions require a real payment checkout and verified webhook.
- External model API: not required for current inference. Trained models run locally from `ml_output/`.

## Validation Commands

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test trading_app.tests
.\.venv\Scripts\python.exe manage.py test trading_app.tests.test_subscription_access trading_app.tests.test_bot_market
.\.venv\Scripts\python.exe manage.py test trading_app.tests_trading_bot.CandlestickStrategyTests
```

## Main Components

- `trading_app/api_views.py`: market data, chart candles, selected market, signal generation, history, analytics, subscription, and bot status APIs.
- `trading_app/trading_bot/technical_analyzer.py`: combines candlestick strategies and SMC analysis.
- `trading_app/trading_bot/candlestick_strategies.py`: candlestick pattern rules.
- `trading_app/trading_bot/smc_strategy.py`: structure, liquidity sweep, OTE/FVG confluence, and confirmation rules.
- `trading_app/services/adapter_resolver.py`: selects the read-only Deriv market-data adapter.
- `trading_app/services/ticker_bridge.py` and `trading_app/consumers.py`: real-time market tick delivery, with simulated fallback.
- `trading_app/ml/`: local model data collection, training, and inference.
