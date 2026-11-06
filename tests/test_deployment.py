from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_development_compose_gates_api_startup_on_migrations() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["rag-migrate"]["command"] == "alembic upgrade head"
    assert services["rag-migrate"]["depends_on"]["rag-postgres"]["condition"] == ("service_healthy")
    assert services["rag-api"]["depends_on"]["rag-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert all(
        "docker-entrypoint-initdb.d" not in volume for volume in services["rag-postgres"]["volumes"]
    )


def test_production_compose_keeps_the_same_migration_gate() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "rag-migrate:" in compose
    # Compose's !reset explicitly removes the inherited development build;
    # plain YAML null is not the merge guarantee required for immutable images.
    assert compose.count("build: !reset null") == 2
    assert "command: alembic upgrade head" in compose
    assert "condition: service_completed_successfully" in compose
    assert 'restart: "no"' in compose


def test_production_rag_api_and_database_have_no_host_ports() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert compose.count("ports: !reset []") >= 2
    assert "internal: true" in compose
    assert "aliases: [boursamind-rag]" in compose


def test_production_compose_sets_explicit_database_pool_budget() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "BOURSAMIND_RAG_DATABASE_POOL_SIZE" in compose
    assert "BOURSAMIND_RAG_DATABASE_MAX_OVERFLOW" in compose
    assert "BOURSAMIND_RAG_DATABASE_POOL_TIMEOUT_SECONDS" in compose
    assert "BOURSAMIND_RAG_DATABASE_CONNECT_TIMEOUT_SECONDS" in compose


def test_rag_image_and_production_services_are_non_root_and_hardened() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "USER appuser" in dockerfile
    assert "pip install --requirement requirements.lock" in dockerfile
    for service in ("rag-migrate", "rag-api"):
        block = compose.split(f"  {service}:", 1)[1]
        assert "read_only: true" in block
        assert "cap_drop: [ALL]" in block
        assert "no-new-privileges:true" in block


def test_docker_context_excludes_local_secrets_and_artifacts() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {".env", ".env.*", ".venv/", "var/", "tests/"} <= ignored


def test_dependency_install_survives_transient_registry_failures() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "PIP_DEFAULT_TIMEOUT=120" in dockerfile
    assert "PIP_RETRIES=5" in dockerfile
    assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
