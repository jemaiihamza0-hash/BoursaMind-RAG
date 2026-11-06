"""Embedding provider abstraction."""

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

from app.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    tokens: int
    model: str
    provider: str


class EmbeddingProvider(Protocol):
    model: str
    provider: str

    async def embed(self, texts: list[str]) -> EmbeddingResult: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


class DeterministicEmbeddingProvider:
    model = "deterministic-hash-v1"
    provider = "deterministic"

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = _TOKEN_RE.findall((text or "").lower())
        for token in tokens:
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[self._embed_one(text) for text in texts],
            tokens=sum(estimate_tokens(text) for text in texts),
            model=self.model,
            provider=self.provider,
        )


class OpenAIEmbeddingProvider:
    provider = "openai"

    def __init__(self, *, api_key: str, model: str, dim: int) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=get_settings().external_http_timeout_seconds,
            max_retries=2,
        )
        self.model = model
        self.dim = dim

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        response = await self._client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dim,
        )
        tokens = (
            response.usage.total_tokens
            if response.usage
            else sum(estimate_tokens(text) for text in texts)
        )
        return EmbeddingResult(
            vectors=[item.embedding for item in response.data],
            tokens=tokens,
            model=self.model,
            provider=self.provider,
        )


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.openai_enabled:
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
            dim=settings.embedding_dim,
        )
    return DeterministicEmbeddingProvider(dim=settings.embedding_dim)


def vector_norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def cosine_similarity(
    a: list[float], b: list[float], *, norm_a: float | None = None
) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = norm_a if norm_a is not None else vector_norm(a)
    nb = vector_norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
