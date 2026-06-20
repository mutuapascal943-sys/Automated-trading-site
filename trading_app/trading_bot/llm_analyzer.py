from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from .interface import Candle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMAnalysisResult:
    bias: str  # bullish | bearish | neutral
    confidence: float  # 0.0 - 1.0
    rationale: str


class LLMProvider(Protocol):
    def chat_complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        ...


class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4") -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def chat_complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=kwargs.get("temperature", 0.3),
            max_tokens=kwargs.get("max_tokens", 500),
        )
        return response.choices[0].message.content or ""


class LLMAnalyzer:
    SYSTEM_PROMPT = (
        "You are a forex and crypto market analyst. "
        "Given recent OHLCV candle data and optional indicators, "
        "produce a structured qualitative assessment of the market. "
        "Respond ONLY with JSON in this format:\n"
        '{"bias": "bullish" | "bearish" | "neutral", '
        '"confidence": 0.0-1.0, '
        '"rationale": "brief explanation of key factors"}'
    )

    def __init__(
        self,
        provider: LLMProvider,
        max_candles_in_context: int = 30,
    ) -> None:
        self._provider = provider
        self._max_candles = max_candles_in_context

    def analyze(
        self,
        symbol: str,
        candles: list[Candle],
        additional_context: str | None = None,
    ) -> LLMAnalysisResult:
        recent = candles[-self._max_candles:]
        summary = self._summarize_candles(symbol, recent)

        user_content = summary
        if additional_context:
            user_content += f"\n\nAdditional context:\n{additional_context}"

        try:
            raw = self._provider.chat_complete([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ])
            parsed = json.loads(raw)
            return LLMAnalysisResult(
                bias=parsed.get("bias", "neutral"),
                confidence=float(parsed.get("confidence", 0)),
                rationale=parsed.get("rationale", ""),
            )
        except Exception as e:
            logger.warning("LLM analysis failed: %s", e)
            return LLMAnalysisResult(bias="neutral", confidence=0.0, rationale=f"Analysis error: {e}")

    def _summarize_candles(self, symbol: str, candles: list[Candle]) -> str:
        if not candles:
            return f"No candle data for {symbol}."

        opens = [float(c.open) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]
        vols = [float(c.volume) for c in candles]

        first = candles[0]
        last = candles[-1]
        change_pct = ((last.close - first.open) / first.open) * 100

        sma_short = sum(closes[-min(5, len(closes)):]) / min(5, len(closes))
        sma_long = sum(closes) / len(closes)
        atr = sum(h - l for h, l in zip(highs, lows)) / len(closes)

        body = (
            f"Symbol: {symbol}\n"
            f"Period: {candles[0].timestamp.isoformat()} to {candles[-1].timestamp.isoformat()}\n"
            f"Candles: {len(candles)} ({candles[0].granularity}s each)\n"
            f"Price range: {min(lows):.5f} - {max(highs):.5f}\n"
            f"Open: {first.open:.5f}, Close: {last.close:.5f}\n"
            f"Change: {change_pct:+.2f}%\n"
            f"SMA(5): {sma_short:.5f}, SMA({len(closes)}): {sma_long:.5f}\n"
            f"ATR: {atr:.5f}\n"
            f"Volume range: {min(vols):.2f} - {max(vols):.2f}\n"
            f"Recent closes: {[f'{c:.5f}' for c in closes[-5:]]}\n"
        )

        if len(closes) >= 10:
            body += f"10-period trends: {'upward' if closes[-1] > closes[-10] else 'downward'}\n"

        return body
