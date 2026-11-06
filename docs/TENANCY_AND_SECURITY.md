# Tenancy And Security

Tenant isolation is enforced on every retrievable table:

- `workspaces.tenant_id`
- `collections.tenant_id`
- `documents.tenant_id`
- `document_chunks.tenant_id`
- `usage_events.tenant_id`

API keys are stored as SHA-256 hashes with a non-secret prefix for lookup. Cross-tenant reads are rejected because all document, chunk, collection, and source lookups check the authenticated tenant.

Prompt-injection defense treats document text as evidence, not instructions. Suspicious document lines are removed from generated excerpts and remain available as source text for audit.

Current upload safety:

- extension allow-list: `txt`, `md`, `pdf`
- size cap: `BOURSAMIND_RAG_MAX_UPLOAD_BYTES`
- path stripping for uploaded filenames
- PDF text extraction via `pypdf`

No API keys or full sensitive documents are written to usage rows.

## Credential and workspace boundaries

Explicit API-key scopes are enforced: `query:read` permits document/source and
retrieval reads, `documents:write` permits ingestion and collection/workspace
creation, and `usage:read` permits operational status, jobs and usage reads.
An empty scope list grants none of these permissions. Existing keys whose scope
value is null retain their legacy full-tenant access; operators should issue
explicitly scoped keys for new integrations. Scopes do not grant access to a
different tenant. A workspace is an organization/filter boundary within the
tenant, not a separate firm permission boundary.

New key lookup prefixes are SHA-256 fingerprints, preventing collisions between
credentials for the same tenant. Authentication also accepts the stored legacy
text prefix and still checks the full hash and revocation state. No credential
rotation or database rewrite is required for compatibility.

The configured Meta Page connector and data-source diagnostics belong to the
platform operator. `/v1/external/meta/page`, `/v1/external/meta/posts` and
`/v1/data-sources/status` require the platform bootstrap credential. An arbitrary
tenant key cannot consume that connector or learn Page metadata. The same
platform credential, plus an explicit user scope, protects financial memory.

Document replacement marks the document pending/failed until indexing succeeds.
Old chunks remain stored for recovery but are excluded from current retrieval.
Explicit citation lookup still permits review of a previously cited stored
passage by the owning tenant. Workspace and collection parameters must agree.

HTTP exception responses never expose debug tracebacks. Unhandled provider errors
record their type, request correlation and route without exception text; provider
exceptions can otherwise contain credentials or private excerpts.
## Visibility within a tenant

Tenant isolation remains the first boundary. `document_visibility_grants` adds
an optional owner boundary inside a tenant for application records shared by
several authorized users. Retrieval and citation services enforce the same
owner predicate. The application must pass a stable, non-secret owner key; the
RAG service does not infer ownership from document metadata.

Visibility mutations use durable tenant-scoped idempotency records and a row
lock on the document. The operation record and grant/document changes commit in
one transaction so a failed attempt can be retried. An owner grant is not a
legal mandate, firm membership, or permission to expose another owner's data.
