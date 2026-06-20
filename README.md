# Forex AI Pro

AI-powered automated forex and crypto trading platform with real-time market analysis, multi-broker support, paper trading, and LLM-driven insights.

## Features

- **Automated Trading** — Broker-agnostic trading engine with live and paper trading modes
- **Multi-Broker Support** — Deriv (WebSocket) and Binance (REST + WebSocket), with a pluggable adapter interface
- **AI Market Analysis** — GPT-4 powered analysis of OHLCV candle data with structured BUY/SELL/HOLD signals
- **RAG Knowledge Engine** — Upload documents, get AI trading insights grounded in your own reference material
- **Risk Management** — Position sizing (fixed, percent balance, Kelly criterion, volatility-adjusted), drawdown limits, daily trade caps, automatic stop-loss/take-profit
- **Backtesting** — Feed historical data, plug in a strategy, get P&L/equity curve/drawdown metrics
- **Two-Factor Authentication** — Email-based OTP 2FA with "Remember Me" persistent sessions
- **Subscription Tiers** — FREE, BASIC, PRO (Stripe-ready)
- **Real-Time Dashboard** — SPA frontend with market watch, bot control, analytics, history, and subscription management
- **Celery Async Tasks** — Scheduled bot cycles, trade sync, daily resets, OTP cleanup

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, Django 5, Django REST Framework |
| Frontend | Vanilla JavaScript SPA, custom CSS (dark theme) |
| Database | SQLite (dev), PostgreSQL (production) |
| Cache/Queue | Redis, Celery + Celery Beat |
| AI/LLM | OpenAI API (GPT-4, text-embedding-3-small) |
| Auth | Email OTP 2FA, bcrypt, Fernet credential encryption |
| Broker APIs | Deriv (WebSocket), Binance (REST + WebSocket) |

## Getting Started

### Prerequisites

- Python 3.10+
- Redis (optional for dev, local-memory fallback available)

### Setup

```bash
# Clone the repository
git clone https://github.com/your-username/forex-ai-pro.git
cd forex-ai-pro

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate    # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env .env.local
# Edit .env.local with your secrets (API keys, email, database, etc.)

# Run migrations
python manage.py migrate

# Create a superuser
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic

# Start development server
python manage.py runserver
```

### Running Background Tasks

```bash
# Terminal 1: Celery worker
celery -A forex_ai_pro worker -l info

# Terminal 2: Celery beat scheduler
celery -A forex_ai_pro beat -l info
```

### Running Tests

```bash
# Core app tests
python manage.py test trading_app

# Trading bot-specific tests (paper broker, risk engine, backtesting, LLM analyzer)
python manage.py test trading_app.tests_trading_bot
```

## Project Structure

```
forex_ai_pro/              # Django project configuration
  settings.py              #   App settings, middleware, installed apps
  celery.py                #   Celery app configuration
  urls.py                  #   Root URL routing

trading_app/               # Main Django application
  models.py                #   User, Trade, Signal, RiskConfig, Subscription, RAG, etc.
  views.py                 #   Web UI views (dashboard, auth, profiles)
  api_views.py             #   REST API endpoints
  tasks.py                 #   Celery background tasks
  forms.py                 #   Django forms (registration, login, password reset)
  serializers.py           #   DRF serializers
  decorators.py            #   2FA enforcement decorators

trading_app/services/      # Business logic layer
  llm_service.py           #   OpenAI GPT-4 integration
  rag_engine.py            #   Document chunking, embedding, retrieval
  broker_service.py        #   Broker REST API client
  email_service.py         #   OTP generation and email delivery
  cache_service.py         #   Redis/memory cache abstraction
  credential_encrypt.py    #   Fernet encryption for broker credentials
  adapter_resolver.py      #   Broker adapter lookup

trading_app/trading_bot/   # Trading engine
  interface.py             #   Abstract broker adapter + data classes
  paper_broker.py          #   In-memory paper trading simulator
  deriv_adapter.py         #   Deriv WebSocket adapter
  binance_adapter.py       #   Binance REST/WebSocket adapter
  risk_engine.py           #   Position sizing, risk rules, stop-loss/take-profit
  llm_analyzer.py          #   LLM-driven market analysis from candle data
  backtest.py              #   Historical backtesting engine
  symbol_map.py            #   Canonical-to-broker symbol mapping

static/                    # Frontend assets
  css/style.css            #   Dark theme design system
  js/app.js                #   Single-page application logic

templates/                 # Django templates
  base.html                #   Base layout
  dashboard/               #   SPA dashboard panels
  registration/            #   Auth pages (login, register, 2FA, password reset)
```

## Environment Variables

Key variables in `.env`:

| Variable | Description |
|----------|-------------|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DJANGO_DEBUG` | Debug mode toggle |
| `EMAIL_HOST*` | SMTP configuration for 2FA email |
| `OPENAI_API_KEY` | OpenAI API key |
| `TRADING_API_*` | Broker API credentials |
| `REDIS_URL` | Redis connection for cache/Celery |
| `DATABASE_URL` | Database connection string |

## API

REST endpoints exposed via Django REST Framework:

- `GET/POST /api/users/` — User management
- `GET/POST /api/trades/` — Trade history
- `GET/POST /api/signals/` — Trading signals
- `GET/POST /api/rag-documents/` — RAG document management
- `POST /api/llm-query/` — Send LLM prompts
- `POST /api/backtest/` — Run backtest simulation
- `POST /api/auth/*` — Authentication endpoints

## License

MIT
