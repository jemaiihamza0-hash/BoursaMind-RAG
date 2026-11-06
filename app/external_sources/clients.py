"""Small, defensive HTTP clients for INS, Open-Meteo and GDELT."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)


class ExternalSourceError(RuntimeError):
    """A provider could not be reached or returned an unusable payload."""

    def __init__(
        self,
        message: str,
        *,
        status: str = "provider_unavailable",
        retryable: bool = False,
        required_permission: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.required_permission = required_permission


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if len(text) == 16 and text.endswith("Z") and "T" in text:
            parsed = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        else:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


class BaseClient:
    source = "external"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self._client = client

    def _get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                if self._client is not None:
                    response = self._client.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=self.timeout,
                    )
                else:
                    with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                        response = client.get(url, params=params, headers=headers)
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as exc:
                    raise ExternalSourceError(
                        f"{self.source} returned malformed JSON",
                        status="invalid_response",
                    ) from exc
            except httpx.HTTPStatusError as exc:
                error = self._http_error(exc.response)
                if not error.retryable or attempt == self.max_retries:
                    logger.warning("%s returned HTTP %s", self.source, exc.response.status_code)
                    raise error from exc
            except httpx.TimeoutException as exc:
                if attempt == self.max_retries:
                    logger.warning("%s request timed out", self.source)
                    raise ExternalSourceError(
                        f"{self.source} request timed out",
                        status="provider_unavailable",
                        retryable=True,
                    ) from exc
            except httpx.HTTPError as exc:
                if attempt == self.max_retries:
                    logger.warning("%s request failed", self.source)
                    raise ExternalSourceError(
                        f"{self.source} request failed",
                        status="provider_unavailable",
                        retryable=True,
                    ) from exc
            logger.info("Retrying %s request after attempt %s", self.source, attempt + 1)
            time.sleep(min(0.1 * (2**attempt), 0.5))
        raise ExternalSourceError(f"{self.source} request failed")

    def _get_payload(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                if self._client is not None:
                    response = self._client.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=self.timeout,
                    )
                else:
                    with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                        response = client.get(url, params=params, headers=headers)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").casefold()
                if "xml" in content_type:
                    return self._parse_xml(response.text)
                try:
                    return response.json()
                except ValueError as exc:
                    raise ExternalSourceError(
                        f"{self.source} returned malformed JSON",
                        status="invalid_response",
                    ) from exc
            except httpx.HTTPStatusError as exc:
                error = self._http_error(exc.response)
                if not error.retryable or attempt == self.max_retries:
                    logger.warning("%s returned HTTP %s", self.source, exc.response.status_code)
                    raise error from exc
            except ElementTree.ParseError as exc:
                raise ExternalSourceError(
                    f"{self.source} returned malformed XML",
                    status="invalid_response",
                ) from exc
            except httpx.TimeoutException as exc:
                if attempt == self.max_retries:
                    raise ExternalSourceError(
                        f"{self.source} request timed out",
                        status="provider_unavailable",
                        retryable=True,
                    ) from exc
            except httpx.HTTPError as exc:
                if attempt == self.max_retries:
                    raise ExternalSourceError(
                        f"{self.source} request failed",
                        status="provider_unavailable",
                        retryable=True,
                    ) from exc
            time.sleep(min(0.1 * (2**attempt), 0.5))
        raise ExternalSourceError(f"{self.source} request failed")

    def _parse_xml(self, text: str) -> dict[str, Any]:
        root = ElementTree.fromstring(text)

        def convert(element: ElementTree.Element) -> Any:
            children = list(element)
            if not children:
                return (element.text or "").strip()
            grouped: dict[str, Any] = {}
            for child in children:
                value = convert(child)
                if child.tag in grouped:
                    if not isinstance(grouped[child.tag], list):
                        grouped[child.tag] = [grouped[child.tag]]
                    grouped[child.tag].append(value)
                else:
                    grouped[child.tag] = value
            return grouped

        return {root.tag: convert(root)}

    def _http_error(self, response: httpx.Response) -> ExternalSourceError:
        return ExternalSourceError(
            f"{self.source} returned HTTP {response.status_code}",
            status="provider_unavailable",
            retryable=response.status_code >= 500 or response.status_code == 429,
        )


class OpenMeteoClient(BaseClient):
    source = "open_meteo"

    def forecast(
        self, *, latitude: float, longitude: float, location: str, forecast_days: int = 3
    ) -> dict[str, Any]:
        variables = (
            "temperature_2m,relative_humidity_2m,precipitation,rain,wind_speed_10m,weather_code"
        )
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": variables,
            "hourly": variables,
            "forecast_days": forecast_days,
            "timezone": "UTC",
        }
        payload = self._get_json(self.base_url, params)
        if not isinstance(payload, dict) or not isinstance(payload.get("current"), dict):
            raise ExternalSourceError("open_meteo response has no current object")
        payload["location"] = location
        return payload

    def normalize(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        current = payload.get("current") or {}
        observed = _timestamp(current.get("time")) or datetime.now(UTC)
        location = payload.get("location")
        mapping = {
            "temperature_2m": ("temperature", "°C"),
            "relative_humidity_2m": ("relative_humidity", "%"),
            "precipitation": ("precipitation", "mm"),
            "rain": ("rain", "mm"),
            "wind_speed_10m": ("wind_speed", "km/h"),
            "weather_code": ("weather_code", None),
        }
        return [
            {
                "source": self.source,
                "event_type": "weather",
                "location": location,
                "metric": metric,
                "value": current[key],
                "unit": unit,
                "observed_at": observed,
                "external_id": f"{location}:{metric}:{observed.isoformat()}",
                "raw_data": payload,
            }
            for key, (metric, unit) in mapping.items()
            if key in current and isinstance(current[key], (int, float))
        ]


class GdeltClient(BaseClient):
    source = "gdelt"

    def search(
        self, query: str, *, timespan: str = "7d", maxrecords: int = 50, sort: str = "datedesc"
    ) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query must not be empty")
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "timespan": timespan,
            "maxrecords": maxrecords,
            "sort": sort,
        }
        payload = self._get_json(self.base_url, params)
        if not isinstance(payload, dict):
            raise ExternalSourceError("gdelt response must be a JSON object")
        articles = payload.get("articles", [])
        if not isinstance(articles, list):
            raise ExternalSourceError("gdelt articles must be a list")
        payload["articles"] = articles
        return payload

    def normalize(
        self, payload: dict[str, Any], *, company: str | None = None
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        for article in payload.get("articles", []):
            if not isinstance(article, dict):
                continue
            url = article.get("url") or article.get("sourceurl")
            if not url or url in seen:
                continue
            seen.add(url)
            title = article.get("title") or article.get("seendate") or "GDELT article"
            events.append(
                {
                    "source": self.source,
                    "event_type": "news",
                    "title": str(title)[:500],
                    "description": article.get("snippet") or article.get("description"),
                    "company": company,
                    "published_at": _timestamp(
                        article.get("seendate") or article.get("published_at")
                    ),
                    "source_url": str(url),
                    "external_id": str(url),
                    "raw_data": article,
                }
            )
        return events


class InsClient(BaseClient):
    """Configurable INS foundation; no undocumented endpoint is assumed."""

    source = "ins"

    def fetch(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        path = path.strip()
        if not path:
            raise ValueError("INS path must be configured")
        if path.startswith("http://") or path.startswith("https://"):
            raise ValueError("INS query path must be relative to INS_BASE_URL")
        url = f"{self.base_url}/{path.lstrip('/')}"
        return self._get_payload(url, params)

    def normalize(
        self, payload: Any, *, indicator: str, location: str = "Tunisia", period: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self._extract_rows(payload)
        if not isinstance(rows, list):
            rows = [rows]
        events = []
        for row in rows:
            if isinstance(row, dict):
                value = self._first_number(row)
                row_period = (
                    row.get("period")
                    or row.get("date")
                    or row.get("annee")
                    or row.get("year")
                    or period
                )
            else:
                value, row_period = row, period
            if not isinstance(value, (int, float)):
                continue
            events.append(
                {
                    "source": self.source,
                    "event_type": "economic_indicator",
                    "indicator": indicator,
                    "location": location,
                    "value": value,
                    "period": str(row_period) if row_period is not None else None,
                    "external_id": f"{indicator}:{location}:{row_period}:{value}",
                    "raw_data": row if isinstance(row, dict) else {"value": row},
                }
            )
        return events

    def _extract_rows(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        if isinstance(payload.get("data"), list):
            return payload["data"]
        if "row" in payload:
            row = payload["row"]
            return row if isinstance(row, list) else [row]
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("records"), list):
            return result["records"]
        for value in payload.values():
            if isinstance(value, dict):
                nested = self._extract_rows(value)
                if nested:
                    return nested
            if isinstance(value, list):
                return value
        return []

    def _first_number(self, row: dict[str, Any]) -> float | int | None:
        preferred = ("value", "value_numeric", "valeur", "population", "total", "nombre")
        for key in preferred:
            if key in row:
                value = self._coerce_number(row[key])
                if value is not None:
                    return value
        for key, raw in row.items():
            if str(key).startswith("_"):
                continue
            value = self._coerce_number(raw)
            if value is not None:
                return value
        return None

    def _coerce_number(self, value: Any) -> float | int | None:
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            cleaned = value.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None


class MetaGraphClient(BaseClient):
    """Permission-aware Meta Graph API client using bearer authentication."""

    source = "meta"
    POSTS_PERMISSION = "pages_read_engagement"
    USER_CONTENT_PERMISSION = "pages_read_user_content"

    def __init__(
        self,
        base_url: str,
        *,
        version: str,
        page_id: str,
        page_access_token: str,
        granted_permissions: set[str] | None = None,
        timeout: float = 15.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            base_url,
            timeout=timeout,
            max_retries=max_retries,
            client=client,
        )
        self.version = version.strip().strip("/")
        self.page_id = page_id.strip()
        self._page_access_token = page_access_token.strip()
        self.granted_permissions = granted_permissions or set()

    @property
    def configured(self) -> bool:
        return bool(self.page_id and self._page_access_token)

    def capabilities(self, *, page_metadata_verified: bool = False) -> dict[str, str]:
        return {
            "page_metadata": (
                "available"
                if page_metadata_verified
                else "configured_not_tested"
                if self.configured
                else "not_configured"
            ),
            "page_posts": (
                "configured_not_tested"
                if self.POSTS_PERMISSION in self.granted_permissions
                else f"requires_{self.POSTS_PERMISSION}"
            ),
            "user_content": (
                "configured_not_tested"
                if self.USER_CONTENT_PERMISSION in self.granted_permissions
                else f"requires_{self.USER_CONTENT_PERMISSION}"
            ),
        }

    def permission_statuses(self) -> dict[str, str]:
        permissions = (
            "pages_show_list",
            "business_management",
            self.POSTS_PERMISSION,
            self.USER_CONTENT_PERMISSION,
        )
        return {
            permission: ("configured" if permission in self.granted_permissions else "missing")
            for permission in permissions
        }

    def get_page_info(self) -> dict[str, str | None]:
        self._require_configured()
        payload = self._graph_get(self.page_id, fields="id,name,category")
        if not isinstance(payload, dict) or not payload.get("id") or not payload.get("name"):
            raise ExternalSourceError(
                "meta returned invalid Page metadata",
                status="invalid_response",
            )
        return {
            "id": str(payload["id"]),
            "name": str(payload["name"]),
            "category": str(payload["category"]) if payload.get("category") else None,
        }

    def get_page_posts(self, *, limit: int = 25) -> dict[str, Any]:
        self._require_configured()
        if self.POSTS_PERMISSION not in self.granted_permissions:
            raise ExternalSourceError(
                "meta Page posts require pages_read_engagement",
                status="permission_missing",
                required_permission=self.POSTS_PERMISSION,
            )
        try:
            payload = self._graph_get(
                f"{self.page_id}/posts",
                fields="id,message,created_time,permalink_url",
                limit=limit,
            )
        except ExternalSourceError as exc:
            if exc.status == "permission_missing" and not exc.required_permission:
                raise ExternalSourceError(
                    "meta Page posts require pages_read_engagement",
                    status="permission_missing",
                    required_permission=self.POSTS_PERMISSION,
                ) from exc
            raise
        if not isinstance(payload, dict) or not isinstance(payload.get("data", []), list):
            raise ExternalSourceError(
                "meta returned invalid posts data",
                status="invalid_response",
            )
        return payload

    def normalize_posts(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        for post in payload.get("data", []):
            if not isinstance(post, dict) or not post.get("id"):
                continue
            post_id = str(post["id"])
            if post_id in seen:
                continue
            seen.add(post_id)
            safe_raw = self._redact_credentials(post)
            events.append(
                {
                    "source": self.source,
                    "event_type": "social_post",
                    "description": post.get("message"),
                    "published_at": _timestamp(post.get("created_time")),
                    "source_url": post.get("permalink_url"),
                    "external_id": post_id,
                    "raw_data": safe_raw,
                }
            )
        return events

    def _redact_credentials(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._redact_credentials(item)
                for key, item in value.items()
                if "token" not in key.casefold() and "secret" not in key.casefold()
            }
        if isinstance(value, list):
            return [self._redact_credentials(item) for item in value]
        if isinstance(value, str) and self._page_access_token and value == self._page_access_token:
            return "[REDACTED]"
        return value

    def _graph_get(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}/{self.version}/{path.lstrip('/')}"
        return self._get_json(
            url,
            params=params,
            headers={"Authorization": f"Bearer {self._page_access_token}"},
        )

    def _require_configured(self) -> None:
        if not self.configured:
            raise ExternalSourceError(
                "meta Page credentials are not configured",
                status="not_configured",
            )

    def _http_error(self, response: httpx.Response) -> ExternalSourceError:
        graph_error: dict[str, Any] = {}
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                graph_error = payload["error"]
        except ValueError:
            return ExternalSourceError(
                "meta returned malformed JSON",
                status="invalid_response",
            )

        code = graph_error.get("code")
        error_type = str(graph_error.get("type") or "")
        message = str(graph_error.get("message") or "")
        if code in {10, 200, 299}:
            return ExternalSourceError(
                "meta permission is missing",
                status="permission_missing",
            )
        if code == 100 and (
            "permission" in message.casefold()
            or "pages_read_engagement" in message
            or "Page Public Metadata Access" in message
        ):
            return ExternalSourceError(
                "meta permission is missing",
                status="permission_missing",
                required_permission=(
                    self.POSTS_PERMISSION if "pages_read_engagement" in message else None
                ),
            )
        if code == 190 or (not code and "OAuth" in error_type):
            return ExternalSourceError(
                "meta authentication failed",
                status="authentication_error",
            )
        if code in {4, 17, 32, 613} or response.status_code == 429:
            return ExternalSourceError(
                "meta rate limit reached",
                status="rate_limited",
                retryable=True,
            )
        if code == 100:
            return ExternalSourceError(
                "meta Page ID or request is invalid",
                status="invalid_response",
            )
        return ExternalSourceError(
            f"meta returned HTTP {response.status_code}",
            status="provider_unavailable",
            retryable=response.status_code >= 500,
        )
