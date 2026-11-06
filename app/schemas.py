"""Pydantic request and response models for the BoursaMind-RAG API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TenantCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=2, max_length=255)
    contact_email: str | None = Field(default=None, max_length=320)
    monthly_query_quota: int = Field(default=0, ge=0)
    monthly_document_quota: int = Field(default=0, ge=0)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    status: str
    contact_email: str | None = None
    monthly_query_quota: int = 0
    monthly_document_quota: int = 0
    created_at: datetime


class TenantCreatedOut(BaseModel):
    tenant: TenantOut
    api_key: str
    api_key_prefix: str


class WorkspaceCreate(BaseModel):
    slug: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    settings: dict[str, Any] = Field(default_factory=dict)


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    slug: str
    name: str
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CollectionCreate(BaseModel):
    workspace_id: int | None = None
    slug: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    metadata_schema: dict[str, Any] = Field(default_factory=dict)


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    workspace_id: int
    slug: str
    name: str
    description: str | None = None
    metadata_schema: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ChunkingOptions(BaseModel):
    chunk_size: int | None = Field(default=None, ge=100, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    strategy: str = Field(default="paragraph", max_length=40)


class DocumentCreate(BaseModel):
    workspace_id: int | None = None
    collection_id: int | None = None
    external_id: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1)
    source_url: str | None = Field(default=None, max_length=1000)
    document_type: str = Field(default="TEXT", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)
    owner_keys: list[str] | None = Field(default=None, min_length=1, max_length=100)
    index_now: bool = True
    chunking: ChunkingOptions | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    workspace_id: int
    collection_id: int
    external_id: str | None = None
    title: str
    source_url: str | None = None
    document_type: str
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    workspace_id: int | None = None
    collection_id: int | None = None
    document_id: int | None = None
    retry_of_job_id: int | None = None
    attempt: int = 1
    status: str
    chunks_created: int
    chunks_deleted: int
    embedding_tokens: int
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class DocumentCreatedOut(BaseModel):
    document: DocumentOut
    job: JobOut | None = None


class DocumentDeleteOut(BaseModel):
    deleted: bool
    chunks_deleted: int = 0


class DocumentVisibilityChange(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120)
    external_id: str = Field(min_length=1, max_length=255)
    owner_key: str = Field(min_length=1, max_length=180)
    visible: bool


class DocumentVisibilityOut(BaseModel):
    operation_id: str
    external_id: str
    owner_key: str
    visible: bool
    idempotent: bool = False
    document_deleted: bool = False
    chunks_deleted: int = 0
    remaining_owners: int = 0


class IndexRequest(BaseModel):
    workspace_id: int | None = None
    collection_ids: list[int] = Field(default_factory=list)
    document_ids: list[int] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=500)


class IndexRunOut(BaseModel):
    jobs_created: int = 0
    documents_indexed: int = 0
    chunks_created: int = 0
    chunks_deleted: int = 0
    embedding_tokens: int = 0
    embedding_model: str
    status: str = "SUCCESS"
    errors: list[str] = Field(default_factory=list)


class QueryRequest(BaseModel):
    workspace_id: int | None = None
    collection_ids: list[int] = Field(default_factory=list)
    query: str = Field(min_length=1, max_length=4000)
    filters: dict[str, Any] = Field(default_factory=dict)
    visibility_owner: str | None = Field(default=None, min_length=1, max_length=180)
    conversation_id: str | None = Field(default=None, max_length=255)
    top_k: int = Field(default=5, ge=1, le=50)
    min_relevance: float | None = Field(default=None, ge=0.0, le=1.0)
    include_answer: bool = True


class CitationOut(BaseModel):
    chunk_id: int
    document_id: int
    collection_id: int
    workspace_id: int
    passage_index: int
    page_number: int | None = None
    title: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    document_type: str
    reporting_period: str | None = None
    issuer: str | None = None
    document_version: int | None = None
    statement_scope: str | None = None
    validation_status: str | None = None
    extraction_method: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float
    vector_similarity: float
    lexical_score: float
    is_official: bool = False


class RetrievalResultOut(BaseModel):
    chunk_id: int
    content: str
    score: float
    vector_similarity: float
    lexical_score: float
    metadata_match: float
    citation: CitationOut


class QueryUsageOut(BaseModel):
    embedding_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    provider: str | None = None


class QueryResponse(BaseModel):
    request_id: str
    answer: str
    citations: list[CitationOut] = Field(default_factory=list)
    sources: list[CitationOut] = Field(default_factory=list)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    usage: QueryUsageOut = Field(default_factory=QueryUsageOut)
    abstained: bool = False
    abstain_reason: str | None = None


class SourceOut(BaseModel):
    citation: CitationOut
    content: str


class StatusOut(BaseModel):
    service: str
    database: str
    vector_backend: str
    embedding_model: str
    llm_provider: str
    tenants: int
    workspaces: int
    collections: int
    documents: int
    chunks: int
    jobs: dict[str, int] = Field(default_factory=dict)


class HealthOut(BaseModel):
    status: str
    checks: dict[str, str]
    version: str


class UsageOut(BaseModel):
    tenant: str
    requests: int
    documents: int
    chunks: int
    embedding_tokens: int
    input_tokens: int
    output_tokens: int
    by_operation: dict[str, int] = Field(default_factory=dict)


class OperationalActivityOut(BaseModel):
    """A safe, tenant-scoped RAG operation summary for observability clients."""

    request_id: str
    operation: str
    status_code: int
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    chunks_retrieved: int = 0
    provider: str | None = None
    error_code: str | None = None
    created_at: datetime


class OperationalJobOut(BaseModel):
    """Indexing state without raw provider errors or document contents."""

    id: int
    document_id: int | None = None
    retry_of_job_id: int | None = None
    attempt: int = 1
    status: str
    chunks_created: int = 0
    embedding_tokens: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    error_code: str | None = None


class OperationalJobsPageOut(BaseModel):
    items: list[OperationalJobOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class OperationalJobRetryOut(BaseModel):
    original_job_id: int
    job: OperationalJobOut
    idempotent: bool = False


class DiagnosticsOut(BaseModel):
    generated_at: datetime
    requests_24h: int = 0
    successful_requests_24h: int = 0
    failed_requests_24h: int = 0
    success_rate_24h: float | None = None
    average_latency_ms_24h: int | None = None
    last_request_at: datetime | None = None
    last_index_attempt_at: datetime | None = None
    last_successful_index_at: datetime | None = None
    pending_documents: int = 0
    failed_documents: int = 0
    recent_activity: list[OperationalActivityOut] = Field(default_factory=list)
    recent_jobs: list[OperationalJobOut] = Field(default_factory=list)


class EvaluationCase(BaseModel):
    id: str
    question: str
    expected_terms: list[str] = Field(default_factory=list)
    expected_document_title: str | None = None
    should_abstain: bool = False
    filters: dict[str, Any] = Field(default_factory=dict)


class EvaluationCaseResult(BaseModel):
    id: str
    passed: bool
    observed: str
    best_score: float = 0.0
    context_precision: float = 0.0
    context_recall: float = 0.0
    citation_correctness: float = 0.0
    faithfulness: float = 0.0
    context_relevance: float = 0.0
    answer_relevance: float = 0.0
    detail: str = ""


class EvaluationReport(BaseModel):
    dataset: str
    passed: int
    total: int
    pass_rate: float
    metrics: dict[str, float]
    cases: list[EvaluationCaseResult]
