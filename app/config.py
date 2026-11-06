"""Configuration for the standalone BoursaMind-RAG service."""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INS_BASE_URL = "https://catalog.data.gov.tn"
DEFAULT_INS_QUERY_PATH = "fr/api/3/action/datastore_search"
DEFAULT_INS_INDICATOR = "population_kelibia_2004_2014"
DEFAULT_INS_RESOURCE_ID = "f1c31070-d2dc-429d-a5bf-31cedd4768dd"


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().upper()
    return normalized.startswith(("REPLACE_", "REQUIRED_SECRET", "CHANGE_ME"))


class Settings(BaseSettings):
    """Environment-backed runtime settings.

    Every public setting is prefixed with ``BOURSAMIND_RAG_`` so this service can
    run next to BoursaMind without stealing its environment variables.
    """

    model_config = SettingsConfigDict(
        env_prefix="BOURSAMIND_RAG_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "BoursaMind-RAG"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    api_prefix: str = "/v1"

    database_url: str = "sqlite:///./var/boursamind_rag.db"
    database_pool_size: int = 4
    database_max_overflow: int = 1
    database_pool_timeout_seconds: float = 30.0
    database_pool_recycle_seconds: int = 1800
    database_connect_timeout_seconds: int = 10
    vector_backend: str = "json"
    redis_url: str = ""

    api_key_header: str = "X-BoursaMind-RAG-Key"
    compatibility_api_key_header: str = "X-BoursaMind-Key"
    admin_key_header: str = "X-BoursaMind-RAG-Admin-Key"
    admin_key: str = ""
    bootstrap_api_key: str = "dev-boursamind-rag-key"
    bootstrap_tenant_slug: str = "boursamind"
    bootstrap_tenant_name: str = "BoursaMind"

    default_workspace_name: str = "Default Workspace"
    default_collection_name: str = "Default Collection"

    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"
    embedding_dim: int = 1536
    embedding_provider: str = "auto"
    llm_provider: str = "deterministic"
    openai_daily_token_budget: int = 0

    chunk_target_tokens: int = 700
    chunk_overlap_tokens: int = 120
    chunk_min_tokens: int = 80
    default_top_k: int = 5
    max_top_k: int = 50
    retrieval_candidate_pool: int = 600
    min_relevance: float = 0.22
    max_context_chars: int = 12_000

    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_file_extensions: str = "txt,md,pdf"
    create_tables_on_startup: bool = True

    log_level: str = "INFO"

    # Trusted, allowlisted bridge to the BoursaMind API.  Empty by default: a
    # standalone RAG deployment must never be able to impersonate an investor.
    boursamind_api_url: str = ""
    boursamind_service_key: str = ""
    boursamind_api_timeout_seconds: float = 5.0

    # Public external-data providers. The INS-compatible default uses Tunisia's
    # official CKAN DataStore API for a population dataset sourced from INS.
    ins_base_url: str = DEFAULT_INS_BASE_URL
    ins_query_path: str = DEFAULT_INS_QUERY_PATH
    ins_indicator: str = DEFAULT_INS_INDICATOR
    ins_resource_id: str = DEFAULT_INS_RESOURCE_ID
    ins_limit: int = 5
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    gdelt_base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_graph_version: str = "v26.0"
    meta_page_id: str = ""
    meta_page_access_token: str = ""
    meta_user_access_token: str = ""
    meta_granted_permissions: str = "pages_show_list,business_management,public_profile"
    external_http_timeout_seconds: float = 15.0
    external_http_max_retries: int = 2
    gdelt_default_timespan: str = "7d"
    gdelt_default_maxrecords: int = 50
    open_meteo_forecast_days: int = 3
    open_meteo_locations_json: str = (
        '[{"name":"Tunis","latitude":36.8065,"longitude":10.1815},'
        '{"name":"Sfax","latitude":34.7406,"longitude":10.7603},'
        '{"name":"Sousse","latitude":35.8256,"longitude":10.63699},'
        '{"name":"Bizerte","latitude":37.2744,"longitude":9.8739},'
        '{"name":"Nabeul","latitude":36.4561,"longitude":10.7376},'
        '{"name":"Gabes","latitude":33.8815,"longitude":10.0982}]'
    )

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def production_config_issues(self) -> list[str]:
        """Return unsafe live settings without exposing secret values."""
        issues: list[str] = []
        if self.debug:
            issues.append("DEBUG must be false in production")
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
            issues.append("BOURSAMIND_RAG_DATABASE_URL must use PostgreSQL in production")
        if (
            "localhost" in self.database_url
            or "boursamind_rag:boursamind_rag@" in self.database_url
        ):
            issues.append(
                "BOURSAMIND_RAG_DATABASE_URL must not use development credentials or localhost"
            )
        if self.database_pool_size < 1 or self.database_max_overflow < 0:
            issues.append("RAG database pool size must be >= 1 and max overflow >= 0")
        if self.database_pool_timeout_seconds <= 0 or self.database_connect_timeout_seconds <= 0:
            issues.append("RAG database pool/connect timeouts must be positive")
        if self.vector_backend.strip().lower() in {"", "json", "local", "sqlite"}:
            issues.append("BOURSAMIND_RAG_VECTOR_BACKEND must be database in production")
        if self.retrieval_candidate_pool < self.max_top_k:
            issues.append("RAG retrieval candidate pool must be at least max_top_k")
        if (
            len(self.bootstrap_api_key.strip()) < 32
            or self.bootstrap_api_key == "dev-boursamind-rag-key"
            or _is_placeholder(self.bootstrap_api_key)
        ):
            issues.append(
                "BOURSAMIND_RAG_BOOTSTRAP_API_KEY must be a generated secret (>= 32 chars)"
            )
        if len(self.admin_key.strip()) < 32 or _is_placeholder(self.admin_key):
            issues.append("BOURSAMIND_RAG_ADMIN_KEY must be a generated secret (>= 32 chars)")
        if len(self.boursamind_service_key.strip()) < 32 or _is_placeholder(
            self.boursamind_service_key
        ):
            issues.append(
                "BOURSAMIND_RAG_BOURSAMIND_SERVICE_KEY must be a generated secret (>= 32 chars)"
            )
        if not self.boursamind_api_url.strip() or "localhost" in self.boursamind_api_url:
            issues.append("BOURSAMIND_RAG_BOURSAMIND_API_URL must identify the internal backend")
        if self.embedding_provider.strip().lower() == "openai" and not self.openai_api_key.strip():
            issues.append("OpenAI embedding provider requires BOURSAMIND_RAG_OPENAI_API_KEY")
        if self.llm_provider.strip().lower() == "openai" and not self.openai_api_key.strip():
            issues.append("OpenAI LLM provider requires BOURSAMIND_RAG_OPENAI_API_KEY")
        if self.create_tables_on_startup:
            issues.append("BOURSAMIND_RAG_CREATE_TABLES_ON_STARTUP must be false in production")
        return issues

    @model_validator(mode="after")
    def _default_blank_ins_settings(self) -> "Settings":
        if not self.ins_base_url.strip() or self.ins_base_url.strip() == "http://dataportal.ins.tn":
            self.ins_base_url = DEFAULT_INS_BASE_URL
        if not self.ins_query_path.strip():
            self.ins_query_path = DEFAULT_INS_QUERY_PATH
        if not self.ins_indicator.strip():
            self.ins_indicator = DEFAULT_INS_INDICATOR
        if not self.ins_resource_id.strip():
            self.ins_resource_id = DEFAULT_INS_RESOURCE_ID
        return self

    @property
    def open_meteo_locations(self) -> list[dict]:
        try:
            rows = json.loads(self.open_meteo_locations_json)
        except json.JSONDecodeError as exc:
            raise ValueError("BOURSAMIND_RAG_OPEN_METEO_LOCATIONS_JSON is invalid JSON") from exc
        if not isinstance(rows, list):
            raise ValueError("BOURSAMIND_RAG_OPEN_METEO_LOCATIONS_JSON must be a list")
        locations = []
        for row in rows:
            if not isinstance(row, dict) or not {"name", "latitude", "longitude"} <= row.keys():
                raise ValueError("Each weather location needs name, latitude, and longitude")
            locations.append(row)
        return locations

    @property
    def meta_permissions(self) -> set[str]:
        return {
            value.strip() for value in self.meta_granted_permissions.split(",") if value.strip()
        }

    @property
    def meta_configured(self) -> bool:
        return bool(self.meta_page_id.strip() and self.meta_page_access_token.strip())

    @property
    def openai_enabled(self) -> bool:
        if self.environment.strip().lower() == "test":
            return False
        if self.embedding_provider.strip().lower() == "deterministic":
            return False
        return bool(self.openai_api_key.strip())

    @property
    def llm_openai_enabled(self) -> bool:
        return self.openai_enabled and self.llm_provider.strip().lower() == "openai"

    @property
    def allowed_extensions(self) -> set[str]:
        return {
            value.strip().lower().lstrip(".")
            for value in self.allowed_file_extensions.split(",")
            if value.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
