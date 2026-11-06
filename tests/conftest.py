"""Keep the default test suite independent of local provider credentials."""

import os

# Test modules use disposable databases and deterministic providers. Explicitly
# shadow the developer .env bridge so a test never calls their running account.
# Tests for the HTTP bridge may opt in with monkeypatch and a mock transport.
os.environ["BOURSAMIND_RAG_BOURSAMIND_API_URL"] = ""
os.environ["BOURSAMIND_RAG_BOURSAMIND_SERVICE_KEY"] = ""
