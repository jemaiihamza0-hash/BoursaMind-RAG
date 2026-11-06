"""Seed the generic acceptance tenant data through the public API."""

import argparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8001")
    parser.add_argument("--api-key", default="dev-boursamind-rag-key")
    args = parser.parse_args()

    headers = {"X-BoursaMind-RAG-Key": args.api_key}
    payload = {
        "title": "DemoCompany Travel Policy",
        "text": (
            "DemoCompany Travel Policy\n\n"
            "Employees must submit travel receipts within 30 days of the trip. "
            "Finance reimburses approved travel expenses after manager review."
        ),
        "document_type": "POLICY",
        "metadata": {"domain": "demo", "department": "finance"},
        "index_now": True,
    }
    response = httpx.post(f"{args.url}/v1/documents", headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
