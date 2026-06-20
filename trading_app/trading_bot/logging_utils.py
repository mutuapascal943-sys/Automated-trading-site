from __future__ import annotations

import abc
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("trading_bot")


class AuditLogger(abc.ABC):
    @abc.abstractmethod
    def log_order(self, event: str, order_id: str, details: dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def log_signal(self, symbol: str, signal: str, confidence: float, details: dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def log_connection(self, broker: str, event: str, details: dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def log_error(self, source: str, error: str, details: dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def log_risk(self, rule: str, triggered: bool, details: dict[str, Any]) -> None:
        ...


class ConsoleAuditLogger(AuditLogger):
    def log_order(self, event: str, order_id: str, details: dict[str, Any]) -> None:
        self._emit("order", {"event": event, "order_id": order_id, **details})

    def log_signal(self, symbol: str, signal: str, confidence: float, details: dict[str, Any]) -> None:
        self._emit("signal", {"symbol": symbol, "signal": signal, "confidence": confidence, **details})

    def log_connection(self, broker: str, event: str, details: dict[str, Any]) -> None:
        self._emit("connection", {"broker": broker, "event": event, **details})

    def log_error(self, source: str, error: str, details: dict[str, Any]) -> None:
        self._emit("error", {"source": source, "error": error, **details})

    def log_risk(self, rule: str, triggered: bool, details: dict[str, Any]) -> None:
        self._emit("risk", {"rule": rule, "triggered": triggered, **details})

    def _emit(self, category: str, data: dict[str, Any]) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            **data,
        }
        logger.info(json.dumps(record))


class NullAuditLogger(AuditLogger):
    def log_order(self, event: str, order_id: str, details: dict[str, Any]) -> None: ...
    def log_signal(self, symbol: str, signal: str, confidence: float, details: dict[str, Any]) -> None: ...
    def log_connection(self, broker: str, event: str, details: dict[str, Any]) -> None: ...
    def log_error(self, source: str, error: str, details: dict[str, Any]) -> None: ...
    def log_risk(self, rule: str, triggered: bool, details: dict[str, Any]) -> None: ...
