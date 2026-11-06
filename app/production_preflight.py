"""Secret-free production configuration gate for BoursaMind-RAG."""

from __future__ import annotations

import logging

from app.config import get_settings

logger = logging.getLogger("boursamind_rag.production_preflight")


def preflight() -> list[str]:
    settings = get_settings()
    issues: list[str] = []
    if not settings.is_production:
        issues.append("BOURSAMIND_RAG_ENVIRONMENT must be production")
    issues.extend(settings.production_config_issues())
    return list(dict.fromkeys(issues))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    issues = preflight()
    if issues:
        for issue in issues:
            logger.error("rag_production_preflight_failed issue=%s", issue)
        return 1
    logger.info("rag_production_preflight_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
