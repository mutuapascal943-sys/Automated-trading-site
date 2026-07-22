import os
import json
import logging
from typing import Optional
from decouple import config
import google.generativeai as genai

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        self.api_key = config('GEMINI_API_KEY', default='')
        self.model = config('GEMINI_MODEL', default='gemini-2.0-flash')
        self._genai_configured = False

    def _ensure_configured(self):
        if not self._genai_configured:
            if not self.api_key or self.api_key.startswith('your-'):
                return False
            genai.configure(api_key=self.api_key)
            self._genai_configured = True
        return True

    def is_configured(self) -> bool:
        return self._ensure_configured()

    def _generate_json(self, system_prompt: str, user_content: str, temperature: float = 0.3, max_tokens: int = 500) -> dict:
        if not self.is_configured():
            return {'error': 'LLM not configured'}

        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system_prompt,
        )

        generation_config = genai.GenerationConfig(
            response_mime_type='application/json',
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        try:
            response = model.generate_content(
                user_content,
                generation_config=generation_config,
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f'Gemini generation failed: {e}')
            return {'error': str(e)}

    def analyze_market(self, market_data: str, context: str = '') -> dict:
        if not self.is_configured():
            return {'error': 'LLM not configured', 'signal': 'NONE', 'confidence': 0}

        system_prompt = (
            'You are an expert Forex and crypto trading analyst. '
            'Analyze the provided market data and context, then output a JSON object with: '
            '{"signal": "BUY"/"SELL"/"HOLD", "confidence": 0-100, "reasoning": "str", "stop_loss": "str", "take_profit": "str", "risk_level": "LOW"/"MEDIUM"/"HIGH"}'
        )

        result = self._generate_json(system_prompt, f'Market Data:\n{market_data}\n\nContext:\n{context}')
        if 'error' in result:
            return {'error': result['error'], 'signal': 'NONE', 'confidence': 0}
        return result

    def generate_trading_idea(self, prompt: str) -> dict:
        if not self.is_configured():
            return {'error': 'LLM not configured'}

        system_prompt = (
            'You are a creative trading strategist. Generate actionable trading ideas based on the user request. '
            'Return JSON with: {"idea": "str", "entry": "str", "exit": "str", "risk_management": "str", "timeframe": "str"}'
        )

        return self._generate_json(system_prompt, prompt, temperature=0.7, max_tokens=800)

    def rag_query(self, query: str, context_chunks: list[str]) -> str:
        if not self.is_configured():
            return 'LLM not configured'

        system_prompt = (
            'You are a trading knowledge assistant. Use the provided document context to answer the user query accurately. '
            'If the context does not contain enough information, say so.'
        )

        context = '\n\n'.join([f'Document {i+1}:\n{chunk}' for i, chunk in enumerate(context_chunks)])
        user_content = f'Context:\n{context}\n\nQuery: {query}'

        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system_prompt,
        )

        generation_config = genai.GenerationConfig(
            temperature=0.2,
            max_output_tokens=1000,
        )

        try:
            response = model.generate_content(
                user_content,
                generation_config=generation_config,
            )
            return response.text
        except Exception as e:
            logger.error(f'RAG query failed: {e}')
            return f'Error: {str(e)}'

    def analyze_sentiment(self, text: str) -> str:
        if not self.is_configured():
            return 'LLM not configured'

        system_prompt = (
            'You are a financial market sentiment analyst. '
            'Analyze the provided text for market sentiment indicators. '
            'Consider: news, social media posts, economic data, or any market-related text. '
            'Output a JSON object with: '
            '{"sentiment": "bullish"/"bearish"/"neutral", '
            '"confidence": 0-100, '
            '"key_factors": ["factor1", "factor2", ...], '
            '"impact": "positive"/"negative"/"mixed", '
            '"summary": "brief 1-2 sentence summary"}'
        )

        result = self._generate_json(system_prompt, text)
        return json.dumps(result)

    def analyze_with_feedback(
        self,
        symbol: str,
        candles_text: str,
        trade_history: list[dict],
        additional_context: str = '',
    ) -> dict:
        if not self.is_configured():
            return {'error': 'LLM not configured', 'signal': 'NONE', 'confidence': 0}

        feedback_section = ''
        if trade_history:
            recent = trade_history[-10:]
            wins = sum(1 for t in recent if t.get('pnl', 0) > 0)
            losses = sum(1 for t in recent if t.get('pnl', 0) < 0)
            avg_pnl = sum(t.get('pnl', 0) for t in recent) / len(recent) if recent else 0
            feedback_section = (
                f'\n\nRecent trade history for {symbol}:\n'
                f'Last 10 trades: {wins} wins, {losses} losses, avg PnL: {avg_pnl:.2f}\n'
                f'Recent signals and outcomes:\n'
            )
            for t in recent[-5:]:
                outcome = 'WIN' if t.get('pnl', 0) > 0 else 'LOSS'
                feedback_section += (
                    f'- {t.get("action", "?")} {t.get("symbol", "?")} '
                    f'confidence={t.get("confidence", "?")}%, '
                    f'PnL={t.get("pnl", 0):.2f} ({outcome})\n'
                )

        system_prompt = (
            'You are an expert Forex and crypto trading analyst. '
            'You have access to historical trade outcomes for this symbol. '
            'Use this feedback to improve your analysis. '
            'If past signals at similar confidence levels resulted in losses, be more conservative. '
            'If past signals were profitable, maintain your approach. '
            'Output a JSON object with: '
            '{"signal": "BUY"/"SELL"/"HOLD", "confidence": 0-100, "reasoning": "str", '
            '"stop_loss": "str", "take_profit": "str", "risk_level": "LOW"/"MEDIUM"/"HIGH", '
            '"lesson_learned": "brief note on what past trades suggest"}'
        )

        user_content = f'Market Data:\n{candles_text}'
        if feedback_section:
            user_content += feedback_section
        if additional_context:
            user_content += f'\n\nAdditional context:\n{additional_context}'

        result = self._generate_json(system_prompt, user_content, max_tokens=600)
        if 'error' in result:
            return {'error': result['error'], 'signal': 'NONE', 'confidence': 0}
        return result


llm_service = LLMService()
