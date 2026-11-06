# External data sources

The external-data layer is independent from document embeddings and financial impact analysis. It collects public facts into the tenant-scoped `external_events` table while preserving provider payloads for later processing.

## Open-Meteo

`OpenMeteoClient` requests current and hourly temperature, relative humidity, precipitation, rain, wind speed, and WMO weather code. Locations come from `BOURSAMIND_RAG_OPEN_METEO_LOCATIONS_JSON`; the default list contains Tunis, Sfax, Sousse, Bizerte, Nabeul, and Gabes.

Each current metric becomes one normalized weather event. Its identity is location + metric + observation timestamp, so polling the same observation is idempotent.

## GDELT DOC 2.0

`GdeltClient` always uses `mode=artlist`, `format=json`, and `sort=datedesc`. The caller supplies a generic query, bounded timespan, and bounded record count. It is suitable for company names, aliases, and economic or operational event terms.

Articles without URLs are ignored. Duplicate URLs are removed both within a response and in persistent tenant storage.

## Meta Graph API / Facebook Pages

`MetaGraphClient` uses the configured Graph API version and Page Access Token via an `Authorization: Bearer ...` header. Tokens are never placed in URLs, logs, responses, normalized payloads, or database rows.

Configure placeholders in `.env`:

```env
BOURSAMIND_RAG_META_GRAPH_BASE_URL=https://graph.facebook.com
BOURSAMIND_RAG_META_GRAPH_VERSION=v26.0
BOURSAMIND_RAG_META_PAGE_ID=
BOURSAMIND_RAG_META_PAGE_ACCESS_TOKEN=
BOURSAMIND_RAG_META_GRANTED_PERMISSIONS=pages_show_list,business_management,public_profile
```

The current capability posture is progressive:

| Capability | State |
| --- | --- |
| `pages_show_list` | configured/granted by the operator |
| `business_management` | configured/granted by the operator |
| Page metadata | available when Page ID and Page Access Token are valid |
| `pages_read_engagement` | missing/not tested unless explicitly configured; required for Page posts |
| `pages_read_user_content` | missing/not tested unless explicitly configured; required for user comments/content |

Endpoints:

```text
GET /v1/external/meta/page
GET /v1/external/meta/posts?limit=25
```

When `pages_read_engagement` is absent, the posts endpoint returns `status=permission_missing` and the required permission. It does not retry the permission failure or return an empty success response. Comments are not collected in this phase.

## INS Tunisia

The INS client does not guess an endpoint. Configure a documented relative path using `BOURSAMIND_RAG_INS_QUERY_PATH` and name the returned indicator using `BOURSAMIND_RAG_INS_INDICATOR`. Absolute paths are rejected so the endpoint cannot become an arbitrary server-side request proxy.

The first normalizer accepts a list or a JSON object containing `data`, with numeric values under `value` or `value_numeric` and an optional `period` or `date`. Add a provider-specific adapter when an official INS product uses a different documented schema.

## Reliability

Clients use bounded timeouts and retry timeouts, transport errors, and HTTP 5xx responses. HTTP 4xx and malformed JSON fail immediately with a normalized provider error. A failure affects only the requested provider endpoint.

## API examples

```powershell
$headers = @{ "X-BoursaMind-RAG-Key" = "dev-boursamind-rag-key" }
Invoke-RestMethod http://127.0.0.1:8001/v1/data-sources/status -Headers $headers
Invoke-RestMethod "http://127.0.0.1:8001/v1/external/weather?location=Sfax" -Headers $headers
Invoke-RestMethod "http://127.0.0.1:8001/v1/external/news?query=Tunisair&timespan=7d&maxrecords=50" -Headers $headers
Invoke-RestMethod http://127.0.0.1:8001/v1/external/ins -Headers $headers
Invoke-RestMethod "http://127.0.0.1:8001/v1/external/events?source=gdelt" -Headers $headers
Invoke-RestMethod http://127.0.0.1:8001/v1/external/meta/page -Headers $headers
Invoke-RestMethod "http://127.0.0.1:8001/v1/external/meta/posts?limit=25" -Headers $headers
```
