# Automated Trading Signal Platform

AI-powered automated forex and crypto trading platform with real-time market analysis, paper trading, and rule-based signal generation. Clean, streamlined interface without unnecessary configuration clutter.

## How It Works — End-to-End Flow

### 1. Market Data Ingestion

The bot ingests OHLCV (candle) data from multiple sources:

- **Live Brokers** — Deriv (WebSocket), Binance (REST + WebSocket), MetaTrader 5 (local terminal)
- **Cached Layer** — Candles are cached with a 60-second TTL, avoiding redundant broker calls on every cycle
- **Fallback** — If all brokers are unreachable, the system generates realistic simulated candles using a seeded random-walk model with configurable trend and volatility

Each broker implements a common `BrokerAdapter` interface (`trading_app/trading_bot/interface.py:102`) with 10 methods: `connect`, `disconnect`, `get_candles`, `subscribe_ticks`, `place_order`, `cancel_order`, `get_balance`, `get_open_positions`, `get_trade_history`, and `unsubscribe_ticks`.

### 2. Scheduled Analysis Cycle

A background scheduler (Celery Beat or in-process thread fallback) triggers the bot cycle **every 60 seconds**. The cycle:

1. Queries all users with `trading_enabled=True` and `auto_execute=True`
2. For each user, iterates over their watchlist (default: EUR/USD, GBP/USD, XAU/USD, BTC/USD)
3. Fetches 50 hourly candles from cache or broker
4. Runs technical analysis (see step 3)
5. If a valid signal is generated, routes it through the risk engine

### 3. Signal Generation — Rule-Based Technical Analysis

The core analysis engine (`trading_app/trading_bot/technical_analyzer.py:110`) uses a **weighted voting system** with 5 independent technical indicators, each contributing 20% to the final decision:

| Indicator | Parameters | Logic |
|-----------|-----------|-------|
| **RSI** | 14-period | <30 oversold (+20% bullish), >70 overbought (+20% bearish), 30-40 (+10% bullish), 60-70 (+10% bearish) |
| **SMA Crossover** | 5 vs 20 | Fast above slow = bullish weighted by spread %, capped at 20% |
| **Price Momentum** | 10-bar change | >+0.3% bullish, <-0.3% bearish, gradient-scaled weighting |
| **MACD** | 12/26/9 | Line above signal = bullish, below = bearish, spread-scaled |
| **Bollinger Bands** | 20-period, 2σ | Price at lower band = bullish (oversold), at upper band = bearish (overbought), midline proximity scaled |

**Confidence Calculation**: Sum of bullish vs bearish weights. If one side exceeds 0.15 (out of 1.0 total), that direction becomes the bias. Raw confidence is `bull_weight / 0.80`. This is then adjusted by **trade history feedback** — recent win rates > 60% boost confidence 1.1x, rates < 30% reduce it to 0.8x.

The result includes: `bias` (bullish/bearish/neutral), `confidence` (0.0-1.0), `rationale` (human-readable string of all contributing factors), and all raw indicator values.

### 4. Machine Learning Pipeline (Offline Training)

In addition to rule-based analysis, the platform supports training a **Random Forest classifier** per symbol:

1. **Data Collection** — Paged WebSocket calls to Deriv API, collecting years of OHLCV data (`trading_app/ml/data_collector.py:34`)
2. **Cleaning** — Deduplication, invalid OHLC removal, gap detection, linear interpolation of missing bars (`trading_app/ml/data_cleaner.py:19`)
3. **Feature Engineering** — 25 features including RSI, MACD, ATR, SMA/EMA crossovers, Bollinger Bands, volume ratios, candle body ratios, and price change over multiple horizons (`trading_app/ml/features.py:249`)
4. **Label Generation** — Binary target: 1 if price rises >= 0.1% within the next 4 candles (`trading_app/ml/labels.py:21`)
5. **Training** — RandomForestClassifier (200 trees, max depth 10, balanced class weights) with chronological 70/15/15 split and 5-window walk-forward validation (`trading_app/ml/pipeline.py:182`)
6. **Export** — Pickle + ONNX + metadata JSON, designed for consumption by external MQL5 Expert Advisors

Models are **symbol-aware** (`trading_app/ml/inference.py:29`): each symbol loads its own latest model (exact normalized-name match first, substring match as fallback), so two markets can use different models simultaneously. If no model exists for a symbol, inference returns `None` and the signal falls back to technical analysis only.

### 4b. ML Prediction Feedback Loop

Every directional ML prediction is stored with its **full 25-feature vector** in a `PredictionRecord` (`trading_app/models.py`). A background task (`resolve_pending_predictions` in `trading_app/tasks.py`, every 300s) then:

1. Waits until at least 2× the forecast horizon has elapsed
2. Fetches realized candles from the user's broker
3. Marks the prediction correct/incorrect against actual price movement (`realized_target`)

Resolved records are fed back into training as extra labeled rows, so the model improves over time from real trading outcomes:

```bash
python manage.py train_model --symbol CRASH1000 --granularity 900 --feedback
```

Feedback rows are merged into the training split (`_merge_feedback_rows` in `trading_app/ml/pipeline.py`), with the count recorded in `pipeline_results.json` under the `feedback` step. Paper-mode predictions are marked resolved without an outcome — they're excluded because simulated candles would inject noise into the model.

### 5. Risk Management

Before any trade is executed, it passes through a multi-layered risk engine (`trading_app/trading_bot/risk_engine.py:59`):

- **Position Sizing** — 4 methods: fixed, percent-of-balance, Kelly Criterion, volatility-adjusted
- **Exposure Limit** — Total position volume capped at `max_exposure_percent` of balance
- **Daily Loss Limit** — Halts trading if daily P&L exceeds `max_drawdown` percent
- **Max Open Positions** — Hard limit on simultaneous trades
- **Max Daily Trades** — Configurable daily trade cap
- **Min Confidence Threshold** — Signals below this threshold are silently skipped
- **Custom Rules** — Extensible via `add_rule()`
- **Default Stops** — Auto-applies SL (1%) and TP (2%) if not specified

**SL/TP Calculation** (`trading_app/trading_bot/sl_tp_calculator.py:58`) uses ATR-based dynamic levels scaled by signal confidence:
- SL distance = `ATR * (2.0 - confidence)` — tighter stops for high-confidence signals
- TP distance = `ATR * (1.5 + confidence * 1.5)` — wider targets for high-confidence signals

**Trailing Stops** are applied during position monitoring (every 60s), moving the stop loss in the profit direction once price has moved beyond entry by at least 1x the initial SL distance.

### 6. Trade Execution

- **Paper Mode** — Uses `PaperBrokerAdapter` (`trading_app/trading_bot/paper_broker.py:81`) with configurable slippage simulation, balance tracking, and position management
- **Live Mode** — Routes to the user's configured broker adapter (Deriv, Binance, or MT5), executing market orders with broker-specific order flows:
  - **Deriv**: Two-step propose → buy flow for CFD-style contracts
  - **Binance**: REST order placement with OCO for simultaneous SL/TP
  - **MT5**: Direct `order_send()` to local terminal

### 7. Position Monitoring

A separate monitoring task (`monitor_live_positions` in `trading_app/tasks.py`) runs every 60 seconds and:
- Fetches current price for each open trade
- Computes unrealized P&L
- Checks stop-loss and take-profit hit conditions
- Applies trailing stop adjustments
- Closes trades automatically, updates user balance, and creates notifications

### 8. Real-Time Frontend

The SPA frontend (`static/js/app.js`, ~1280 lines of vanilla JS) connects to the backend via:

- **REST API** — Django REST Framework endpoints for trades, signals, risk config, backtesting, and account management
- **WebSocket** — Django Channels consumer (`trading_app/consumers.py:12`) streams real-time tick data to the browser via a `TickerBridge` (`trading_app/services/ticker_bridge.py:21`) that connects to Deriv WebSocket or falls back to a simulated random-walk generator
- **Charting** — TradingView Lightweight Charts renders candlestick charts with live tick aggregation into 60-second candles, plus overlay lines for entry/SL/TP levels. On load, the chart fetches **real historical candles** from the broker via `GET /api/chart/candles/` (60s granularity to match live aggregation), falling back to a simulated random-walk seed when the broker is unreachable or the user is in paper mode
- **Clean Bot Panel** — Simplified trading bot interface with market selector, paper/live toggle, and real-time signal display. Broker selector, timeframe picker, stake input, and risk settings were removed to keep focus on what matters — the signal
- **Simplified Settings** — Account settings streamlined by removing the Broker API configuration section. No unnecessary fields cluttering the experience

## What Makes This Platform Unique

### 1. Three-Layer Analysis Architecture
Most trading bots rely on a single strategy. This platform has **three independent analysis layers**:
- **Rule-based** (real-time, deterministic, sub-millisecond) for live signal generation
- **ML-based** (Random Forest with 25 features, walk-forward validation, ONNX export) for offline model training and MQL5 integration
- **LLM/RAG infrastructure** (scaffolded with document chunking, embedding storage, and query audit trail) ready for future AI-driven reasoning

### 2. Broker-Agnostic Design
The `BrokerAdapter` interface abstracts away all broker-specific details. Adding a new broker requires implementing just 10 methods. The `AdapterResolver` (`trading_app/services/adapter_resolver.py:18`) auto-selects the right adapter per user. This is not a single-broker tool — it's a **universal trading engine** that happens to ship with Deriv, Binance, and MT5 support.

### 3. Dual-Mode Scheduling
The platform runs on Celery + Redis when available but **gracefully falls back** to an in-process background thread scheduler (`trading_app/scheduler.py:9`) when they're not. No external dependencies are strictly required — the entire platform can run on a single `python manage.py runserver` command.

### 4. Confidence-Scaled Risk
Stop-loss and take-profit levels are not static percentages. They are **dynamically computed** from market volatility (ATR) and scaled by signal confidence. High-confidence signals get tighter stops and wider targets, while low-confidence signals are given more breathing room. This is a direct feedback loop between analysis confidence and risk exposure.

### 5. Candle Caching Layer
Most trading bots re-fetch all candle data on every cycle. This platform implements a **60-second candle cache** that dramatically reduces broker API calls. On the 1-minute cycle, every other run hits the cache instead of the broker, reducing both latency and API rate-limit pressure. Combined with the 1-minute cycle interval (down from 5 minutes), signals arrive up to 5x faster than before.

### 6. Trade History Feedback Loop
The analysis engine adjusts its confidence based on recent trade outcomes. A win rate above 60% boosts confidence by 10%; a rate below 30% reduces it by 20%. This creates a **self-correcting mechanism** that prevents the bot from over-trading during losing streaks.

### 7. Paper Trading That Works Like Live Trading
The `PaperBrokerAdapter` simulates fill slippage (configurable bps), tracks balance and unrealized P&N, and processes SL/TP hits identically to live mode. Trades created in paper mode are real database records with full audit trails — the only difference is the execution path. This means **backtests and paper trades use the same code as live trades**, eliminating divergence between simulated and real performance.

### 8. Enterprise-Grade Security in a Trading Platform
- **Email OTP 2FA** with "Remember Me" persistent sessions
- **Fernet encryption** for stored broker API credentials (PBKDF2-SHA256 key derivation, 600K iterations)
- **Rate limiting** at multiple levels (login, OTP, password reset, API throttling)
- **Disposable email detection** with DNS MX validation during registration
- **Security questions** with SHA-256 hashed answers as account recovery option

### 9. Full Audit Trail
Every signal, trade, risk rule application, broker connection, and error is logged as structured JSON via `ConsoleAuditLogger`. This enables complete reconstruction of trading decisions for analysis or compliance.

### 10. Offline-First ML Pipeline
The machine learning pipeline runs entirely locally — data collection, cleaning, feature engineering, training, and export. No external ML services. The trained model is exported as both Pickle and ONNX, making it usable not just within Django but from any ONNX-compatible runtime (including MQL5 Expert Advisors running inside MetaTrader).

### 11. ML Prediction Feedback Loop
Unlike a static trained model, the ML layer **learns from its own live results**. Each directional prediction is persisted with its feature vector, resolved against realized broker price after the forecast horizon, and appended to the next training run (via `train_model --feedback`). This closes the loop between signal generation and model improvement — the model continuously adapts to the market conditions it actually trades in.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, Django 6, Django REST Framework |
| Frontend | Vanilla JavaScript SPA, Lightweight Charts |
| Database | SQLite (dev), PostgreSQL (production) |
| Cache/Queue | Redis, Celery + Celery Beat |
| Real-Time | Django Channels, Daphne ASGI server |
| ML | scikit-learn, ONNX, skl2onnx |
| Auth | Email OTP 2FA, bcrypt, Fernet encryption |
| Broker APIs | Deriv (WebSocket), Binance (REST + WS), MetaTrader 5 (native) |

## Project Structure

```
forex_ai_pro/                 # Django project configuration
  settings.py                 #   App settings, Celery beat schedule
  celery.py                   #   Celery app configuration
  asgi.py                     #   ASGI entrypoint (Daphne)

trading_app/                  # Main Django application
  models.py                   #   User, Trade, Signal, PredictionRecord, RiskConfig, etc.
  views.py                    #   Web UI views (dashboard, auth, profiles)
  api_views.py                #   REST API endpoints (incl. /api/chart/candles/)
  tasks.py                    #   Celery background tasks (bot cycle, monitoring, prediction resolution)
  forms.py                    #   Registration, login, password reset forms
  serializers.py              #   DRF serializers
  decorators.py               #   2FA enforcement decorators
  consumers.py                #   WebSocket market data consumer
  admin.py                    #   Django admin configuration
  urls.py                     #   URL routing
  routing.py                  #   WebSocket routing
  scheduler.py                #   In-process background scheduler fallback

trading_app/services/         # Business logic
  llm_service.py              #   LLM integration
  rag_engine.py               #   RAG document engine
  broker_service.py           #   Legacy broker REST client
  email_service.py            #   OTP generation and email delivery
  cache_service.py            #   Redis/memory cache abstraction
  credential_encrypt.py       #   Fernet encryption for broker credentials
  adapter_resolver.py         #   Broker adapter lookup
  ticker_bridge.py            #   Real-time tick distribution to WebSocket

trading_app/trading_bot/      # Trading engine core
  interface.py                #   Abstract broker adapter + data classes
  paper_broker.py             #   In-memory paper trading simulator
  deriv_adapter.py            #   Deriv WebSocket adapter
  binance_adapter.py          #   Binance REST/WebSocket adapter
  mt5_adapter.py              #   MetaTrader 5 native adapter
  risk_engine.py              #   Position sizing, risk rules, SL/TP
  technical_analyzer.py       #   Rule-based technical analysis engine
  sl_tp_calculator.py         #   ATR-based stop/target calculator
  backtest.py                 #   Historical backtesting engine
  simulated_candles.py        #   Random-walk candle generator
  logging_utils.py            #   JSON audit logger
  errors.py                   #   Broker error hierarchy
  symbol_map.py               #   Symbol name mapping

trading_app/ml/               # Machine learning pipeline
  pipeline.py                 #   Full training pipeline (incl. feedback-row merge)
  inference.py                #   Symbol-aware model loading + prediction
  features.py                 #   25 technical indicator features
  labels.py                   #   Label generation
  data_collector.py           #   Deriv historical data collection
  data_cleaner.py             #   OHLCV cleaning and validation
  dataset.py                  #   Chronological splits + walk-forward
  feedback.py                 #   Loads resolved predictions as training rows

static/                       # Frontend assets
  css/style.css               #   Dark theme design system
  js/app.js                   #   Single-page application logic

templates/                    # Django templates
  base.html                   #   Base layout
  dashboard/                  #   SPA dashboard panels
  registration/               #   Auth pages
```

## Getting Started

### Prerequisites

- Python 3.10+
- Redis (optional — in-process scheduler fallback available)

### Setup

```bash
git clone <repo-url>
cd Automated-trading-site

python -m venv .venv
.venv\Scripts\activate      # Windows

pip install -r requirements.txt

cp .env .env.local
# Edit .env.local with your configuration

python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic
python manage.py runserver
```

### Running Background Tasks

With Celery + Redis:
```bash
celery -A forex_ai_pro worker -l info
celery -A forex_ai_pro beat -l info
```

Without Redis (auto-fallback to in-process scheduler):
```bash
# Set BROKER_REACHABLE=False in .env.local
python manage.py runserver
# Scheduler starts automatically in a background thread
```

### Training ML Models

Train a fresh model per symbol (no feedback):

```bash
python manage.py train_model \
  --symbol EURUSD \
  --granularity 900 \
  --years 2 \
  --horizon 4 \
  --threshold 0.1 \
  --windows 5
```

Retrain including resolved live predictions (feedback loop):

```bash
python manage.py train_model \
  --symbol CRASH1000 \
  --granularity 900 \
  --years 2 \
  --feedback
```

The `--feedback` flag pulls resolved `PredictionRecord`s from the database (`trading_app/ml/feedback.py`), aligns them to the model's 25 feature columns, and merges them into the training split before fitting.

## Testing

```bash
python manage.py test trading_app
python manage.py test trading_app.tests_trading_bot
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DJANGO_DEBUG` | Debug mode toggle |
| `BROKER_REACHABLE` | Set to `False` to disable Celery/Redis |
| `EMAIL_HOST*` | SMTP configuration |
| `GEMINI_API_KEY` | Google Gemini API key (reserved) |
| `REDIS_URL` | Redis connection string |
| `DATABASE_URL` | Database connection string |

## License

MIT
