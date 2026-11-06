# Contributing

## Development checks

```bash
python -m pip install -r requirements-ci.txt
python -m ruff check app tests
python -m pytest
```

## Change rules

- Keep BoursaMind-RAG independent from BoursaMind Python modules and tables.
- Add an Alembic migration for every production schema change.
- Preserve tenant and owner filters on every new read path.
- Keep credentials and document content out of logs and errors.
- Add tests for security boundaries, migrations and no-answer behavior.
- Do not replace grounded abstention with unsupported generated claims.
- Update API and operations documentation when contracts change.

Never commit environments, production credentials, database files, vector
stores, caches or virtual environments.
