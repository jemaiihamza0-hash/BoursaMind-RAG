"""Run a smoke query against a local BoursaMind-RAG instance."""

import argparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8001")
    parser.add_argument("--api-key", default="dev-boursamind-rag-key")
    parser.add_argument(
        "--query",
        default="How many days do employees have to submit travel receipts?",
    )
    args = parser.parse_args()

    response = httpx.post(
        f"{args.url}/v1/query",
        headers={"X-BoursaMind-RAG-Key": args.api_key},
        json={"query": args.query, "top_k": 5},
        timeout=30,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
