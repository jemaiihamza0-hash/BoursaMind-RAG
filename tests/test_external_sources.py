from __future__ import annotations

import httpx
import pytest

from app.external_sources.clients import (
    ExternalSourceError,
    GdeltClient,
    InsClient,
)


def mocked_client(payload, *, status_code: int = 200, content_type: str = "application/json"):
    def handler(request: httpx.Request) -> httpx.Response:
        if content_type == "application/json":
            return httpx.Response(status_code, json=payload, request=request)
        return httpx.Response(
            status_code,
            text=str(payload),
            headers={"content-type": content_type},
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_gdelt_parsing_deduplicates_articles():
    client = GdeltClient(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        client=mocked_client(
            {
                "articles": [
                    {"url": "https://news.test/a", "title": "A", "seendate": "20260826T120000Z"},
                    {"url": "https://news.test/a", "title": "A duplicate"},
                    {"url": "https://news.test/b", "title": "B", "snippet": "Details"},
                    {"title": "Missing URL"},
                ]
            }
        ),
    )
    events = client.normalize(client.search("Tunisair"), company="Tunisair")
    assert [event["source_url"] for event in events] == [
        "https://news.test/a",
        "https://news.test/b",
    ]
    assert events[0]["published_at"].year == 2026
    assert events[0]["company"] == "Tunisair"


def test_ins_connector_is_configurable_and_normalizes_rows():
    client = InsClient(
        "http://dataportal.ins.tn",
        client=mocked_client({"data": [{"value": 8.7, "period": "2026-07"}]}),
    )
    events = client.normalize(client.fetch("api/indicator"), indicator="inflation")
    assert events[0]["source"] == "ins"
    assert events[0]["indicator"] == "inflation"
    assert events[0]["value"] == 8.7
    with pytest.raises(ValueError):
        client.fetch("https://other.example/api")


def test_ins_normalizes_ckan_records_and_french_numeric_values():
    client = InsClient("https://catalog.data.gov.tn")
    events = client.normalize(
        {
            "success": True,
            "result": {
                "records": [
                    {"annee": "2014", "population": "57 070"},
                    {"annee": "2004", "valeur": "43,209"},
                ]
            },
        },
        indicator="population_kelibia_2004_2014",
    )
    assert [event["value"] for event in events] == [57070.0, 43.209]
    assert events[0]["period"] == "2014"
    assert events[0]["external_id"].startswith("population_kelibia_2004_2014:Tunisia:2014")


def test_ins_fetch_parses_xml_payloads():
    client = InsClient(
        "https://catalog.data.gov.tn",
        client=mocked_client(
            "<rows><row><annee>2014</annee><population>57070</population></row></rows>",
            content_type="application/xml",
        ),
    )
    events = client.normalize(client.fetch("api/xml"), indicator="population")
    assert events[0]["value"] == 57070.0


@pytest.mark.parametrize(
    "response",
    [
        {"status_code": 503, "payload": {"error": "down"}},
        {"status_code": 200, "payload": "not json", "content_type": "text/plain"},
    ],
)
def test_external_http_errors_and_malformed_json(response):
    client = GdeltClient(
        "https://gdelt.test",
        max_retries=0,
        client=mocked_client(
            response["payload"],
            status_code=response["status_code"],
            content_type=response.get("content_type", "application/json"),
        ),
    )
    with pytest.raises(ExternalSourceError):
        client.search("Tunisia")


def test_empty_gdelt_response_is_valid_empty_collection():
    client = GdeltClient("https://gdelt.test", client=mocked_client({"articles": []}))
    assert client.normalize(client.search("Tunisia")) == []


def test_timeout_is_translated_to_source_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = GdeltClient(
        "https://gdelt.test",
        max_retries=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ExternalSourceError, match="timed out"):
        client.search("Tunisia")
