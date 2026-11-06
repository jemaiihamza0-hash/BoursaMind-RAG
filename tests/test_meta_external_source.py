from __future__ import annotations

import json

import httpx
import pytest

from app.external_sources.clients import ExternalSourceError, MetaGraphClient

TOKEN = "test-page-token-redacted"


def meta_client(
    payload,
    *,
    status_code: int = 200,
    permissions: set[str] | None = None,
    content_type: str = "application/json",
    requests: list[httpx.Request] | None = None,
) -> MetaGraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if content_type == "application/json":
            return httpx.Response(status_code, json=payload, request=request)
        return httpx.Response(status_code, text=str(payload), request=request)

    return MetaGraphClient(
        "https://graph.facebook.com",
        version="v26.0",
        page_id="12345",
        page_access_token=TOKEN,
        granted_permissions=permissions or set(),
        max_retries=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_meta_page_metadata_and_status_parsing():
    requests: list[httpx.Request] = []
    client = meta_client(
        {"id": "12345", "name": "BoursaMind Test", "category": "Finance"},
        requests=requests,
    )
    assert client.get_page_info() == {
        "id": "12345",
        "name": "BoursaMind Test",
        "category": "Finance",
    }
    request = requests[0]
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)


def test_meta_missing_configuration():
    client = MetaGraphClient(
        "https://graph.facebook.com",
        version="v26.0",
        page_id="",
        page_access_token="",
    )
    assert client.configured is False
    with pytest.raises(ExternalSourceError) as captured:
        client.get_page_info()
    assert captured.value.status == "not_configured"


@pytest.mark.parametrize(
    "error_payload",
    [
        {
            "error": {
                "message": "Invalid OAuth access token.",
                "type": "OAuthException",
                "code": 190,
            }
        },
        {
            "error": {
                "message": "Session expired",
                "type": "OAuthException",
                "code": 190,
                "error_subcode": 463,
            }
        },
    ],
)
def test_meta_invalid_or_expired_token(error_payload):
    client = meta_client(error_payload, status_code=400)
    with pytest.raises(ExternalSourceError) as captured:
        client.get_page_info()
    assert captured.value.status == "authentication_error"
    assert TOKEN not in str(captured.value)


def test_meta_permission_denied():
    client = meta_client(
        {"error": {"message": "Permissions error", "type": "OAuthException", "code": 10}},
        status_code=403,
        permissions={MetaGraphClient.POSTS_PERMISSION},
    )
    with pytest.raises(ExternalSourceError) as captured:
        client.get_page_posts()
    assert captured.value.status == "permission_missing"
    assert captured.value.required_permission == MetaGraphClient.POSTS_PERMISSION


def test_meta_code_100_page_metadata_permission_message_is_permission_missing():
    client = meta_client(
        {
            "error": {
                "message": (
                    "Object does not exist, cannot be loaded due to missing permission. "
                    "This endpoint requires the 'pages_read_engagement' permission."
                ),
                "type": "OAuthException",
                "code": 100,
            }
        },
        status_code=400,
    )
    with pytest.raises(ExternalSourceError) as captured:
        client.get_page_info()
    assert captured.value.status == "permission_missing"
    assert captured.value.required_permission == "pages_read_engagement"


def test_meta_posts_capability_blocks_missing_permission_without_http_call():
    requests: list[httpx.Request] = []
    client = meta_client({"data": []}, requests=requests)
    with pytest.raises(ExternalSourceError) as captured:
        client.get_page_posts()
    assert captured.value.status == "permission_missing"
    assert captured.value.required_permission == "pages_read_engagement"
    assert requests == []


def test_meta_malformed_json():
    client = meta_client("not-json", content_type="text/plain")
    with pytest.raises(ExternalSourceError) as captured:
        client.get_page_info()
    assert captured.value.status == "invalid_response"


def test_meta_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = MetaGraphClient(
        "https://graph.facebook.com",
        version="v26.0",
        page_id="12345",
        page_access_token=TOKEN,
        max_retries=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ExternalSourceError) as captured:
        client.get_page_info()
    assert captured.value.status == "provider_unavailable"


def test_meta_http_5xx():
    client = meta_client({"error": {"message": "down"}}, status_code=503)
    with pytest.raises(ExternalSourceError) as captured:
        client.get_page_info()
    assert captured.value.status == "provider_unavailable"


def test_meta_post_normalization_deduplicates_and_removes_credentials():
    client = meta_client(
        {"data": []},
        permissions={MetaGraphClient.POSTS_PERMISSION},
    )
    events = client.normalize_posts(
        {
            "data": [
                {
                    "id": "12345_1",
                    "message": "New investment announced",
                    "created_time": "2026-08-26T12:00:00+0000",
                    "permalink_url": "https://facebook.example/posts/1",
                    "access_token": TOKEN,
                    "debug": {"page_token": TOKEN},
                    "unexpected_value": TOKEN,
                },
                {"id": "12345_1", "message": "duplicate"},
            ]
        }
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "social_post"
    assert events[0]["external_id"] == "12345_1"
    assert TOKEN not in json.dumps(events, default=str)
    assert "access_token" not in events[0]["raw_data"]


def test_meta_successful_posts_response():
    client = meta_client(
        {
            "data": [
                {
                    "id": "12345_9",
                    "message": "Official Page post",
                    "created_time": "2026-08-26T12:00:00Z",
                    "permalink_url": "https://facebook.example/posts/9",
                }
            ]
        },
        permissions={MetaGraphClient.POSTS_PERMISSION},
    )
    payload = client.get_page_posts(limit=10)
    events = client.normalize_posts(payload)
    assert events[0]["description"] == "Official Page post"
    assert events[0]["published_at"].tzinfo is not None


def test_meta_capability_diagnostics_are_explicit():
    client = meta_client({"id": "12345", "name": "Page"})
    assert client.capabilities() == {
        "page_metadata": "configured_not_tested",
        "page_posts": "requires_pages_read_engagement",
        "user_content": "requires_pages_read_user_content",
    }
    assert client.capabilities(page_metadata_verified=True)["page_metadata"] == "available"
    assert client.permission_statuses() == {
        "pages_show_list": "missing",
        "business_management": "missing",
        "pages_read_engagement": "missing",
        "pages_read_user_content": "missing",
    }
