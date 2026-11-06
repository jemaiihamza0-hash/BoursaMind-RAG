"""Generic RAG query orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, exists, false, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.auth.security import Principal
from app.config import get_settings
from app.embeddings.providers import cosine_similarity, get_embedding_provider, vector_norm
from app.generation.providers import get_llm_provider
from app.models import (
    Conversation,
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVisibilityGrant,
)
from app.observability import correlation_id_context, log_event, metrics
from app.retrieval import scoring
from app.schemas import CitationOut, QueryResponse, QueryUsageOut, RetrievalResultOut
from app.usage.service import UsageService

logger = logging.getLogger(__name__)


class QueryService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()

    def query(self, principal: Principal, request) -> QueryResponse:
        started = time.perf_counter()
        request_id = correlation_id_context.get() or str(uuid4())
        quota_identity = sha256(f"{request_id}\0{request.query}".encode()).hexdigest()
        UsageService(self.session).reserve_query(
            principal, request_id=request_id, quota_identity=quota_identity
        )
        quota_seconds = time.perf_counter() - started
        provider = get_embedding_provider()
        embedding_started = time.perf_counter()
        embed_result = asyncio.run(provider.embed([request.query]))
        embedding_seconds = time.perf_counter() - embedding_started
        metrics.observe(
            "rag_embedding_duration_seconds", embedding_seconds, provider=provider.provider
        )
        query_vector = embed_result.vectors[0]
        query_tokens = scoring.tokenize(request.query)
        retrieval_started = time.perf_counter()
        candidates = self._candidate_chunks(
            principal,
            request,
            query_vector=query_vector,
            embedding_model=embed_result.model,
            embedding_dimension=len(query_vector),
        )
        retrieval_seconds = time.perf_counter() - retrieval_started
        ranking_started = time.perf_counter()
        ranked = self._rank(candidates, query_vector, query_tokens, request.filters)
        ranking_seconds = time.perf_counter() - ranking_started
        floor = (
            request.min_relevance
            if request.min_relevance is not None
            else self.settings.min_relevance
        )
        top = [row for row in ranked if row["signals"].topical_relevance >= floor][: request.top_k]

        if not top:
            best = max((row["signals"].topical_relevance for row in ranked), default=0.0)
            response = QueryResponse(
                request_id=request_id,
                answer="No relevant source was found in this tenant workspace.",
                citations=[],
                sources=[],
                retrieval={
                    "results": [],
                    "candidates_considered": len(ranked),
                    "min_relevance": floor,
                    "best_score": round(best, 4),
                },
                usage=QueryUsageOut(
                    embedding_tokens=embed_result.tokens,
                    provider=provider.provider,
                ),
                abstained=True,
                abstain_reason=(
                    "No retrieved chunk cleared the relevance threshold "
                    f"({floor:.2f}); best score was {best:.2f}."
                ),
            )
            UsageService(self.session).record(
                principal,
                request_id=request_id,
                operation="query",
                status_code=200,
                latency_ms=int((time.perf_counter() - started) * 1000),
                embedding_tokens=embed_result.tokens,
                chunks_retrieved=0,
                provider=provider.provider,
                flags=["abstained"],
                quota_identity=quota_identity,
            )
            self.session.commit()
            duration = time.perf_counter() - started
            metrics.increment("rag_queries_total", result="abstained", provider=provider.provider)
            metrics.observe("rag_query_duration_seconds", duration, result="abstained")
            log_event(
                logger,
                logging.INFO,
                "rag_query_completed",
                result="abstained",
                provider=provider.provider,
                candidates=len(ranked),
                retrieved=0,
                duration_ms=round(duration * 1000, 2),
                embedding_ms=round(embedding_seconds * 1000, 2),
                quota_ms=round(quota_seconds * 1000, 2),
                retrieval_ms=round(retrieval_seconds * 1000, 2),
                ranking_ms=round(ranking_seconds * 1000, 2),
                generation_ms=0.0,
            )
            return response

        contexts = [{"content": row["chunk"].content, "score": row["signals"].score} for row in top]
        generation_started = time.perf_counter()
        generated = (
            asyncio.run(get_llm_provider().generate(query=request.query, contexts=contexts))
            if request.include_answer
            else None
        )
        generation_seconds = time.perf_counter() - generation_started
        answer = generated.answer if generated else ""
        results = [self._result(row) for row in top]
        citations = [result.citation for result in results]
        response = QueryResponse(
            request_id=request_id,
            answer=answer,
            citations=citations,
            sources=citations,
            retrieval={
                "results": [result.model_dump(mode="json") for result in results],
                "candidates_considered": len(ranked),
                "min_relevance": floor,
                "best_score": round(
                    max((row["signals"].topical_relevance for row in ranked), default=0.0),
                    4,
                ),
                "mode": "hybrid",
            },
            usage=QueryUsageOut(
                embedding_tokens=embed_result.tokens,
                input_tokens=generated.input_tokens if generated else 0,
                output_tokens=generated.output_tokens if generated else 0,
                provider=generated.provider if generated else provider.provider,
            ),
        )
        self._remember_turn(principal, request, response)
        UsageService(self.session).record(
            principal,
            request_id=request_id,
            operation="query",
            status_code=200,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            embedding_tokens=response.usage.embedding_tokens,
            chunks_retrieved=len(citations),
            provider=response.usage.provider,
            quota_identity=quota_identity,
        )
        self.session.commit()
        duration = time.perf_counter() - started
        metrics.increment("rag_queries_total", result="success", provider=response.usage.provider)
        metrics.observe("rag_query_duration_seconds", duration, result="success")
        log_event(
            logger,
            logging.INFO,
            "rag_query_completed",
            result="success",
            provider=response.usage.provider,
            candidates=len(ranked),
            retrieved=len(citations),
            duration_ms=round(duration * 1000, 2),
            embedding_ms=round(embedding_seconds * 1000, 2),
            quota_ms=round(quota_seconds * 1000, 2),
            retrieval_ms=round(retrieval_seconds * 1000, 2),
            ranking_ms=round(ranking_seconds * 1000, 2),
            generation_ms=round(generation_seconds * 1000, 2),
        )
        return response

    def _candidate_chunks(
        self,
        principal: Principal,
        request,
        *,
        query_vector: list[float],
        embedding_model: str,
        embedding_dimension: int,
    ) -> list[DocumentChunk]:
        # Keep old chunks for recovery but never serve a pending/failed replacement.
        query = (
            select(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.tenant_id == principal.tenant.id,
                Document.tenant_id == principal.tenant.id,
                Document.status == DocumentStatus.INDEXED,
                DocumentChunk.embedding_model == embedding_model,
                DocumentChunk.embedding_dimension == embedding_dimension,
            )
        )
        any_grant = exists(
            select(DocumentVisibilityGrant.id).where(
                DocumentVisibilityGrant.tenant_id == principal.tenant.id,
                DocumentVisibilityGrant.document_id == Document.id,
            )
        )
        if request.visibility_owner is None:
            query = query.where(~any_grant)
        else:
            owner_grant = exists(
                select(DocumentVisibilityGrant.id).where(
                    DocumentVisibilityGrant.tenant_id == principal.tenant.id,
                    DocumentVisibilityGrant.document_id == Document.id,
                    DocumentVisibilityGrant.owner_key == request.visibility_owner,
                )
            )
            query = query.where((~any_grant) | owner_grant)
        if request.workspace_id is not None:
            query = query.where(DocumentChunk.workspace_id == request.workspace_id)
        if request.collection_ids:
            query = query.where(DocumentChunk.collection_id.in_(request.collection_ids))
        bind = self.session.get_bind()
        metadata_pushed = bind.dialect.name == "postgresql"
        if metadata_pushed:
            for filter_index, (key, expected) in enumerate(request.filters.items()):
                allowed = (
                    expected.get("$in", [])
                    if isinstance(expected, dict) and "$in" in expected
                    else expected
                    if isinstance(expected, list)
                    else [expected]
                )
                predicates = [
                    DocumentChunk.chunk_metadata.op("@>")(
                        bindparam(
                            f"metadata_filter_{filter_index}_{value_index}",
                            value={key: value},
                            type_=JSONB,
                        )
                    )
                    for value_index, value in enumerate(allowed)
                ]
                query = query.where(or_(*predicates) if predicates else false())
        # Embeddings stay JSON so multiple dimensions can coexist during model
        # migrations. PostgreSQL can still cast compatible rows to pgvector and
        # preselect nearest neighbors before transferring content to the API.
        # Metadata filters remain a Python fallback and retain the wider pool so
        # a matching document is not discarded before those filters are applied.
        candidate_limit = 3000
        params: dict[str, Any] = {}
        if (
            bind.dialect.name == "postgresql"
            and self.settings.vector_backend.strip().lower() == "database"
            and (not request.filters or metadata_pushed)
        ):
            candidate_limit = self.settings.retrieval_candidate_pool
            query = query.order_by(
                text(
                    "CAST(document_chunks.embedding AS text)::vector "
                    "<=> CAST(:retrieval_query_vector AS vector)"
                )
            )
            params["retrieval_query_vector"] = json.dumps(query_vector)
        rows = self.session.execute(query.limit(candidate_limit), params).scalars().all()
        return [
            row
            for row in rows
            if scoring.metadata_allows(request.filters, dict(row.chunk_metadata or {}))
        ]

    def _rank(
        self,
        candidates: list[DocumentChunk],
        query_vector: list[float],
        query_tokens: list[str],
        filters: dict[str, Any],
    ) -> list[dict]:
        if not candidates:
            return []
        tokenized = [scoring.tokenize(chunk.content) for chunk in candidates]
        bm25 = scoring.Bm25Index(tokenized).absolute_scores(query_tokens)
        query_norm = vector_norm(query_vector)
        ranked: list[dict] = []
        for index, chunk in enumerate(candidates):
            metadata = dict(chunk.chunk_metadata or {})
            vector_similarity = cosine_similarity(
                query_vector,
                [float(v) for v in chunk.embedding],
                norm_a=query_norm,
            )
            signals = scoring.rerank(
                vector_similarity=vector_similarity,
                lexical_score=bm25[index],
                coverage=scoring.term_coverage(query_tokens, set(tokenized[index])),
                metadata_match_score=scoring.metadata_match(filters, metadata),
                trust=scoring.source_trust(metadata, is_official=chunk.is_official),
                recency=0.0,
            )
            ranked.append({"chunk": chunk, "signals": signals})
        ranked.sort(key=lambda row: row["signals"].score, reverse=True)
        return ranked

    def _result(self, row: dict) -> RetrievalResultOut:
        chunk: DocumentChunk = row["chunk"]
        signals = row["signals"]
        metadata = dict(chunk.chunk_metadata or {})
        citation = CitationOut(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            collection_id=chunk.collection_id,
            workspace_id=chunk.workspace_id,
            passage_index=chunk.chunk_index,
            page_number=chunk.page_number,
            title=chunk.title,
            source_url=chunk.source_url,
            source_name=chunk.source_name,
            document_type=chunk.document_type,
            reporting_period=metadata.get("reporting_period"),
            issuer=metadata.get("ticker") or metadata.get("company_name"),
            document_version=metadata.get("document_version"),
            statement_scope=metadata.get("statement_scope"),
            validation_status=metadata.get("financial_evidence_status"),
            extraction_method=metadata.get("extraction_method"),
            metadata=metadata,
            score=round(signals.score, 4),
            vector_similarity=round(signals.vector_similarity, 4),
            lexical_score=round(signals.lexical_score, 4),
            is_official=bool(chunk.is_official),
        )
        return RetrievalResultOut(
            chunk_id=chunk.id,
            content=chunk.content,
            score=citation.score,
            vector_similarity=citation.vector_similarity,
            lexical_score=citation.lexical_score,
            metadata_match=round(signals.metadata_match, 4),
            citation=citation,
        )

    def _remember_turn(self, principal: Principal, request, response: QueryResponse) -> None:
        if not request.conversation_id:
            return
        row = (
            self.session.execute(
                select(Conversation).where(
                    Conversation.tenant_id == principal.tenant.id,
                    Conversation.external_id == request.conversation_id,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = Conversation(
                tenant_id=principal.tenant.id,
                workspace_id=request.workspace_id,
                external_id=request.conversation_id,
                turns=[],
            )
            self.session.add(row)
        turns = list(row.turns or [])
        turns.append(
            {
                "query": request.query,
                "request_id": response.request_id,
                "answer": response.answer[:2000],
                "citations": [c.model_dump(mode="json") for c in response.citations],
            }
        )
        row.turns = turns[-20:]
