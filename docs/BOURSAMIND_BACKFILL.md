# BoursaMind Backfill Strategy

BoursaMind owns official Tunisian data collection. The safe migration strategy is:

1. Keep BVMT/CMF/BCT collectors in BoursaMind.
2. Select source rows with extracted text from BoursaMind, especially official publications.
3. Send each document to BoursaMind-RAG through `POST /v1/documents`.
4. Preserve provenance through metadata:
   - `domain`
   - `ticker`
   - `issuer_id`
   - `publication_id`
   - `source`
   - `source_url`
   - `is_official`
5. Re-index from source text rather than copying old vector rows between databases.
6. Verify by querying BoursaMind-RAG and resolving citations through `GET /v1/sources/{chunk_id}`.

This avoids copying financial tables into the generic RAG product and avoids mixing embedding spaces from old and new runtimes.
