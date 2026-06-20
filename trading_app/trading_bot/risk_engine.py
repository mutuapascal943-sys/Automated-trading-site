from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

from .interface import OrderRequest
from .logging_utils import AuditLogger, NullAuditLogger


@dataclass
class PositionSizing:
    method: str = "fixed"  # fixed | percent_balance | kelly | volatility_adjusted
    fixed_volume: Decimal = Decimal("0.01")
    risk_percent: Decimal = Decimal("1")  # percent of balance to risk per trade
    max_risk_percent: Decimal = Decimal("2")
    max_position_size: Decimal = Decimal("100")


@dataclass
class RiskRule:
    name: str
    enabled: bool = True
    params: dict = field(default_factory=dict)
    check: Optional[Callable[..., bool]] = None


class RiskEngine:
    def __init__(
        self,
        sizing: PositionSizing | None = None,
        max_exposure_percent: Decimal = Decimal("20"),
        max_daily_loss_percent: Decimal = Decimal("10"),
        default_stop_loss_percent: Decimal = Decimal("1"),
        default_take_profit_percent: Decimal = Decimal("2"),
        logger: AuditLogger | None = None,
    ) -> None:
        self._sizing = sizing or PositionSizing()
        self._max_exposure_pct = max_exposure_percent
        self._max_daily_loss_pct = max_daily_loss_percent
        self._default_sl_pct = default_stop_loss_percent
        self._default_tp_pct = default_take_profit_percent
        self._logger = logger or NullAuditLogger()
        self._custom_rules: list[RiskRule] = []
        self._daily_pnl = Decimal("0")
        self._current_exposure = Decimal("0")

    def reset_daily(self) -> None:
        self._daily_pnl = Decimal("0")
        self._current_exposure = Decimal("0")

    def apply_rules(
        self,
        symbol: str,
        signal: OrderRequest,
        balance: Decimal,
        open_positions: list | None = None,
    ) -> OrderRequest | None:
        open_positions = open_positions or []

        volume = self._compute_volume(signal.side, balance, signal.volume)

        if volume <= Decimal("0"):
            self._logger.log_risk("position_sizing", triggered=True, details={
                "symbol": symbol, "reason": "zero_volume",
            })
            return None

        signal = OrderRequest(
            symbol=signal.symbol,
            side=signal.side,
            order_type=signal.order_type,
            volume=volume,
            price=signal.price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )

        if not self._check_exposure(volume, balance):
            self._logger.log_risk("max_exposure", triggered=True, details={
                "symbol": symbol, "volume": str(volume), "balance": str(balance),
                "exposure_pct": str(self._current_exposure),
            })
            return None

        if not self._check_daily_loss():
            self._logger.log_risk("daily_loss_limit", triggered=True, details={
                "symbol": symbol, "daily_pnl": str(self._daily_pnl),
            })
            return None

        signal = self._apply_default_stops(symbol, signal)

        for rule in self._custom_rules:
            if not rule.enabled:
                continue
            try:
                if rule.check and not rule.check(symbol=symbol, signal=signal, balance=balance):
                    self._logger.log_risk(rule.name, triggered=True, details={
                        "symbol": symbol, "reason": "custom_rule_blocked",
                    })
                    return None
            except Exception:
                self._logger.log_risk(rule.name, triggered=True, details={
                    "symbol": symbol, "reason": "rule_check_error",
                })
                return None

        self._logger.log_risk("all_rules", triggered=False, details={
            "symbol": symbol, "volume": str(volume),
        })
        return signal

    def add_rule(self, rule: RiskRule) -> None:
        self._custom_rules.append(rule)

    def record_trade_pnl(self, pnl: Decimal) -> None:
        self._daily_pnl += pnl

    def update_exposure(self, exposure: Decimal) -> None:
        self._current_exposure = exposure

    def _compute_volume(self, side: str, balance: Decimal, requested_volume: Decimal) -> Decimal:
        if self._sizing.method == "fixed":
            return min(self._sizing.fixed_volume, self._sizing.max_position_size)

        elif self._sizing.method == "percent_balance":
            pct = self._sizing.risk_percent / Decimal("100")
            volume = (balance * pct) / Decimal("100")
            return min(volume, self._sizing.max_position_size)

        elif self._sizing.method == "volatility_adjusted":
            return min(requested_volume, self._sizing.max_position_size)

        return requested_volume

    def _check_exposure(self, volume: Decimal, balance: Decimal) -> bool:
        if balance <= Decimal("0"):
            return False
        new_exposure = self._current_exposure + volume
        max_exposure = balance * (self._max_exposure_pct / Decimal("100"))
        return new_exposure <= max_exposure

    def _check_daily_loss(self) -> bool:
        max_loss = (self._max_daily_loss_pct / Decimal("100"))
        return self._daily_pnl >= -(max_loss * Decimal("10000"))

    def _apply_default_stops(self, symbol: str, signal: OrderRequest) -> OrderRequest:
        if signal.stop_loss is None and signal.price:
            sl = signal.price * (Decimal("1") - self._default_sl_pct / Decimal("100"))
            signal = OrderRequest(
                symbol=signal.symbol,
                side=signal.side,
                order_type=signal.order_type,
                volume=signal.volume,
                price=signal.price,
                stop_loss=sl,
                take_profit=signal.take_profit,
            )

        if signal.take_profit is None and signal.price:
            tp = signal.price * (Decimal("1") + self._default_tp_pct / Decimal("100"))
            signal = OrderRequest(
                symbol=signal.symbol,
                side=signal.side,
                order_type=signal.order_type,
                volume=signal.volume,
                price=signal.price,
                stop_loss=signal.stop_loss,
                take_profit=tp,
            )

        return signal
