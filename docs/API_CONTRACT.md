# API Contract

## Auth

All tenant-scoped endpoints require `X-BoursaMind-RAG-Key`. The compatibility header `X-BoursaMind-Key` is also accepted.

Bearer authentication is accepted case-insensitively. Explicit key scopes are
enforced as described in [Tenancy and security](TENANCY_AND_SECURITY.md).
Platform Meta diagnostics and financial user memory additionally require the
platform service credential; a generic tenant key is insufficient.

## Core Endpoints

- `POST /v1/documents`: create a document and optionally index it immediately.
- `POST /v1/documents/upload`: upload UTF-8 text/Markdown or PDF.
- `POST /v1/index`: index pending documents in the authenticated tenant.
- `POST /v1/query`: retrieve, rerank, generate a grounded response, and return citations.
- `GET /v1/sources/{chunk_id}`: resolve a citation to its exact passage.
- `GET /v1/status`: service, database, vector, job, document, and chunk counts.
- `GET /v1/usage`: tenant-scoped usage foundation.
- `POST /v1/evaluation/run`: run a named evaluation dataset.

## Query Contract

The query schema is generic. Use `filters` for domain metadata. BoursaMind-specific fields such as ticker and publication ID are not first-class engine concepts.

## No-Answer Contract

When no chunk clears `min_relevance`, `abstained=true`, citations are empty, and `abstain_reason` explains the threshold outcome.

Pending or failed document revisions are not offered as current evidence even if
old chunks remain available for recovery. Financial-query evidence preserves
`OFFICIAL_DOCUMENT` only for citations marked official; other retrieved documents
use the additive `DOCUMENT_EVIDENCE` category. Consumers must inspect each
citation's provenance rather than infer official status from retrieval alone.
## Owner-scoped document visibility

`POST /v1/documents` accepts an optional non-empty `owner_keys` array. When it
is present, search and citation reads require the same `visibility_owner` in
`POST /v1/query` and `GET /v1/sources/{chunk_id}`. Documents created without
owner grants retain tenant-wide visibility for backward compatibility.

`POST /v1/documents/visibility` applies one grant change. The body contains a
tenant-scoped `operation_id`, `external_id`, `owner_key`, and `visible` boolean.
Replaying the same operation and payload is idempotent; reusing its id for a
different payload returns `409`. Removing one grant preserves content used by
other owners. Removing the final existing grant deletes the document and its
chunks atomically. Revoking an owner from a tenant-public document is a no-op.

Successful replacement reindexes the document without reusing an old citation
identifier. Pending or failed replacement content is never returned as current.
