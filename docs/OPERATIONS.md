# Operations and deployment

## Environments

Development may use SQLite and deterministic providers. Production must use
PostgreSQL, the database vector backend, generated secrets and Alembic-managed
migrations. Startup validates these requirements and fails closed when unsafe.

## Production prerequisites

- PostgreSQL with the `vector` extension;
- an immutable BoursaMind-RAG image;
- a dedicated database and database credential;
- distinct bootstrap, admin and BoursaMind service keys of at least 32 characters;
- internal network paths between the two services as required;
- a protected `.env.production` created from `.env.production.example`.

Never commit `.env`, `.env.production`, database volumes or provider tokens.

## Migration procedure

The current head is `0008_remove_weather_events`.

```bash
alembic heads
alembic current
alembic upgrade head
alembic current
```

The production Compose definition models migrations as a one-shot service. The
API depends on its success and uses `CREATE_TABLES_ON_STARTUP=false`.

Before applying a migration, back up the RAG database, record the application
SHA and revision, test restoration, review upgrade/downgrade behavior and run
the migration before the new API starts. Never run a destructive production
downgrade without a separate recovery decision.

## Docker Compose production example

```bash
cp .env.production.example .env.production
# Replace every REQUIRED_SECRET and the immutable image reference.
docker compose --env-file .env.production \
  -f docker-compose.yml -f docker-compose.prod.yml config
docker compose --env-file .env.production \
  -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The production overlay removes host database/API ports, uses private networks,
drops capabilities, prevents privilege escalation, uses read-only containers
and limits logs and processes.

## Health gates

```text
GET /v1/health       Basic database-aware health
GET /v1/health/live  Process liveness
GET /v1/health/ready Dependency and migration readiness
```

A deployment is ready only after readiness succeeds, the expected Alembic head
is active and an authenticated query resolves a known citation.

## Backup and rollback

Back up the PostgreSQL database, protected runtime configuration references,
immutable image digest, Git SHA and migration revision. A source archive is not
a database backup.

Application rollback switches to the previous immutable image and preserves the
database. Database downgrade is never automatic.

## Operational checks

After a release verify health, exact image digest and Git SHA, Alembic head,
authentication/scopes, ingestion and job completion, query/abstention/citation
resolution, isolation, safe diagnostics and BoursaMind remote-RAG integration.

## Provider modes

Deterministic providers support tests and offline acceptance. OpenAI requires an
explicit provider setting and key. Changing embedding models requires reindexing;
old and new vector dimensions are intentionally not mixed.

## Troubleshooting

- **Production startup is rejected**: correct each reported setting; do not weaken validation.
- **Readiness fails**: verify database reachability, migration head and bootstrap tenant.
- **Queries abstain**: inspect document state, embedding identity, filters and relevance threshold.
- **No citations**: confirm tenant, owner and collection visibility.
- **Provider returns 502**: the provider failed independently; core RAG may remain healthy.
