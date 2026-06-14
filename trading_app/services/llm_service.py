import os
import json
import logging
from typing import Optional
from decouple import config
from openai import OpenAI

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        self.api_key = config('OPENAI_API_KEY', default='')
        self.model = config('OPENAI_MODEL', default='gpt-4')
        self.api_endpoint = config('LLM_API_ENDPOINT', default='https://api.openai.com/v1')
        self._client = None

    @property
    def client(self) -> Optional[OpenAI]:
        if not self.api_key or self.api_key.startswith('sk-your'):
            logger.warning('OpenAI API key not configured')
            return None
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_endpoint,
            )
        return self._client

    def is_configured(self) -> bool:
        return self.client is not None

    def analyze_market(self, market_data: str, context: str = '') -> dict:
        if not self.is_configured():
            return {'error': 'LLM not configured', 'signal': 'NONE', 'confidence': 0}

        system_prompt = (
            'You are an expert Forex and crypto trading analyst. '
            'Analyze the provided market data and context, then output a JSON object with: '
            '{"signal": "BUY"/"SELL"/"HOLD", "confidence": 0-100, "reasoning": "str", "stop_loss": "str", "take_profit": "str", "risk_level": "LOW"/"MEDIUM"/"HIGH"}'
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': f'Market Data:\n{market_data}\n\nContext:\n{context}'}
                ],
                temperature=0.3,
                max_tokens=500,
                response_format={'type': 'json_object'},
            )
            result = json.loads(response.choices[0].message.content)
            return result
        except Exception as e:
            logger.error(f'LLM analysis failed: {e}')
            return {'error': str(e), 'signal': 'NONE', 'confidence': 0}

    def generate_trading_idea(self, prompt: str) -> dict:
        if not self.is_configured():
            return {'error': 'LLM not configured'}

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {'role': 'system', 'content': 'You are a creative trading strategist. Generate actionable trading ideas based on the user request. Return JSON with: {"idea": "str", "entry": "str", "exit": "str", "risk_management": "str", "timeframe": "str"}'},
                    {'role': 'user', 'content': prompt}
                ],
                temperature=0.7,
                max_tokens=800,
                response_format={'type': 'json_object'},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f'Trading idea generation failed: {e}')
            return {'error': str(e)}

    def rag_query(self, query: str, context_chunks: list[str]) -> str:
        if not self.is_configured():
            return 'LLM not configured'

        context = '\n\n'.join([f'Document {i+1}:\n{chunk}' for i, chunk in enumerate(context_chunks)])

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {'role': 'system', 'content': 'You are a trading knowledge assistant. Use the provided document context to answer the user query accurately. If the context does not contain enough information, say so.'},
                    {'role': 'user', 'content': f'Context:\n{context}\n\nQuery: {query}'}
                ],
                temperature=0.2,
                max_tokens=1000,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f'RAG query failed: {e}')
            return f'Error: {str(e)}'


llm_service = LLMService()
