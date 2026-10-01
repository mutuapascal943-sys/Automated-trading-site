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
