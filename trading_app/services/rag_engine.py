import re
import hashlib
import logging
from typing import Optional
from decouple import config
import google.generativeai as genai

logger = logging.getLogger(__name__)


class RAGEngine:
    def __init__(self):
        self.api_key = config('GEMINI_API_KEY', default='')
        self.embedding_model = config('RAG_EMBEDDING_MODEL', default='models/text-embedding-004')
        self.chunk_size = int(config('RAG_CHUNK_SIZE', default=1000))
        self.chunk_overlap = int(config('RAG_CHUNK_OVERLAP', default=200))
        self.top_k = int(config('RAG_TOP_K', default=5))
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

    def chunk_text(self, text: str) -> list[dict]:
        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + self.chunk_size, text_len)
            if end < text_len:
                last_period = text.rfind('.', start, end)
                last_newline = text.rfind('\n', start, end)
                split_at = max(last_period, last_newline)
                if split_at > start + self.chunk_size // 2:
                    end = split_at + 1

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunk_id = hashlib.md5(chunk_text.encode()).hexdigest()[:12]
                chunks.append({
                    'id': chunk_id,
                    'text': chunk_text,
                    'start_pos': start,
                    'end_pos': end,
                })

            start = end - self.chunk_overlap if end < text_len else text_len

        return chunks

    def get_embedding(self, text: str) -> Optional[list[float]]:
        if not self.is_configured():
            return None
        try:
            text = text.replace('\n', ' ')[:8000]
            result = genai.embed_content(
                model=self.embedding_model,
                content=text,
            )
            return result['embedding']
        except Exception as e:
            logger.error(f'Embedding generation failed: {e}')
            return None

    def get_embeddings_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        if not self.is_configured():
            return [None] * len(texts)

        results = []
        batch_size = 20
        for i in range(0, len(texts), batch_size):
            batch = [t.replace('\n', ' ')[:8000] for t in texts[i:i + batch_size]]
            try:
                result = genai.embed_content(
                    model=self.embedding_model,
                    content=batch,
                )
                results.extend(result['embedding'])
            except Exception as e:
                logger.error(f'Batch embedding failed: {e}')
                results.extend([None] * len(batch))

        return results

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0
        return dot / (norm_a * norm_b)

    def rank_chunks(self, query: str, chunks: list[dict]) -> list[dict]:
        query_embedding = self.get_embedding(query)
        if not query_embedding:
            return chunks[:self.top_k]

        for chunk in chunks:
            embedding = chunk.get('embedding')
            if embedding is None:
                embedding = self.get_embedding(chunk['text'])
                chunk['embedding'] = embedding

            if embedding:
                chunk['score'] = self.cosine_similarity(query_embedding, embedding)
            else:
                chunk['score'] = 0

        ranked = sorted(chunks, key=lambda x: x.get('score', 0), reverse=True)
        return ranked[:self.top_k]

    def process_document(self, title: str, content: str, source: str = 'manual') -> list[dict]:
        chunks = self.chunk_text(content)

        texts = [chunk['text'] for chunk in chunks]
        embeddings = self.get_embeddings_batch(texts)

        for chunk, embedding in zip(chunks, embeddings):
            chunk['title'] = title
            chunk['source'] = source
            chunk['embedding'] = embedding

        return chunks


rag_engine = RAGEngine()
