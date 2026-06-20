from __future__ import annotations

import json
import os
from pathlib import Path


class SymbolMap:
    def __init__(self, mapping: dict[str, dict[str, str]]) -> None:
        self._mapping = mapping

    def to_broker(self, canonical: str, broker_name: str) -> str:
        broker_symbols = self._mapping.get(canonical)
        if broker_symbols is None:
            raise KeyError(f"Canonical symbol '{canonical}' not found in mapping")
        native = broker_symbols.get(broker_name)
        if native is None:
            raise KeyError(f"Broker '{broker_name}' has no mapping for '{canonical}'")
        return native

    def to_canonical(self, native: str, broker_name: str) -> str:
        for canonical, broker_symbols in self._mapping.items():
            if broker_symbols.get(broker_name) == native:
                return canonical
        raise KeyError(f"No canonical symbol found for '{native}' on broker '{broker_name}'")

    @property
    def canonical_symbols(self) -> list[str]:
        return list(self._mapping.keys())

    def list_brokers(self, canonical: str) -> list[str]:
        broker_symbols = self._mapping.get(canonical)
        if broker_symbols is None:
            raise KeyError(f"Canonical symbol '{canonical}' not found")
        return list(broker_symbols.keys())


def load_symbol_map(path: str | os.PathLike | None = None) -> SymbolMap:
    if path is None:
        path = Path(__file__).parent / "symbol_map.json"
    with open(path, "r") as f:
        data = json.load(f)
    return SymbolMap(data)
