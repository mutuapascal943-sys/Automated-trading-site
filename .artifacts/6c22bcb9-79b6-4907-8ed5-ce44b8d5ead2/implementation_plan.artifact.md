# Implementation Plan - Trading Bot Signal Generation & ML Pipeline Hardening

Enhance the trading bot's rule-based signal generation, confluence gate, risk management, news filtering, and ML override precedence according to the comprehensive audit findings.

## User Review Required

> [!IMPORTANT]
> - All hard-coded thresholds (confidence scores, minimum confluence, reward-to-risk ratio, EMA periods) will be centralized via `RiskConfig` and a unified configuration service.
> - Rule rejections will strictly override ML model confidence outputs.

## Proposed Changes

### Configuration & Risk Model
#### [MODIFY] [models.py](file:///C:/Users/USER/OneDrive/Documents/Project3.2/trading_app/models.py)
- Add new fields to `RiskConfig`: `min_confluence_count` (default 3), `min_reward_risk` (default 2.0), `ema_fast_period` (default 20), `ema_slow_period` (default 40), `news_blackout_enabled` (default True).

### Strategy & Confluence Engine
#### [MODIFY] [choch.py](file:///C:/Users/USER/OneDrive/Documents/Project3.2/trading_app/strategies/choch.py)
- Implement active economic news blackout check via calendar settings / provider.
#### [MODIFY] [consensus.py](file:///C:/Users/USER/OneDrive/Documents/Project3.2/trading_app/strategies/consensus.py) or new [confluence.py]
- Implement 0-5 Confluence Scorer checking (1) unmitigated S/D zone, (2) S/R zone overlap, (3) 61.8%-78.6% Fib band, (4) liquidity sweep, (5) FVG presence.
- Enforce `min_confluence_count` gate.

### Execution & Risk Gate
#### [MODIFY] [risk_engine.py](file:///C:/Users/USER/OneDrive/Documents/Project3.2/trading_app/trading_bot/risk_engine.py) & [sl_tp_calculator.py](file:///C:/Users/USER/OneDrive/Documents/Project3.2/trading_app/trading_bot/sl_tp_calculator.py)
- Enforce minimum reward:risk ratio check (default 1:2).
- Apply symbol-specific spread buffer to stop loss calculation.

### ML & Signal Pipeline Integration
#### [MODIFY] [api_views.py](file:///C:/Users/USER/OneDrive/Documents/Project3.2/trading_app/api_views.py)
- Enforce strict rule rejection override over ML model confidence.
- Store rejection reasons and confluence breakdown for all generated signals.

### Unit Tests
#### [MODIFY] [test_strategies.py](file:///C:/Users/USER/OneDrive/Documents/Project3.2/trading_app/tests/test_strategies.py) or [test_bot_market.py](file:///C:/Users/USER/OneDrive/Documents/Project3.2/trading_app/tests/test_bot_market.py)
- Add unit tests covering scenarios 1 through 7 (momentum vs structure, confluence threshold, unmitigated zone limit block, R:R ratio, news blackout, ML rule override, spread buffer).

## Verification Plan

### Automated Tests
- Run `.venv\Scripts\python.exe manage.py test` to verify all 414 existing tests plus new regression tests.
