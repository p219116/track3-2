# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Creates the Vertex AI Search data store for HR policy RAG and loads the handbook.

Usage:
    uv run python scripts/setup_vector_store.py            # create + load (safe to re-run)
    uv run python scripts/setup_vector_store.py --delete   # delete the data store

Reads GOOGLE_CLOUD_PROJECT, DATA_STORE_ID, DATA_STORE_LOCATION from `.env`.
Uses Application Default Credentials (`gcloud auth application-default login`).

What it does:
  1. Creates a Vertex AI Search data store (GENERIC / structured / search).
  2. Splits docs/policies/*.md into one document per handbook section
     (same chunking as the local search engine, app/rag_engine.py).
  3. Imports the sections (stable IDs: re-running overwrites in place).
  4. Waits until a test query returns results.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.api_core import exceptions  # noqa: E402
from google.cloud import discoveryengine_v1 as de  # noqa: E402

from app import config, vertex_search  # noqa: E402
from app.rag_engine import policy_vector_store  # noqa: E402

BATCH_SIZE = 100  # inline import limit per request
TEST_QUERY = "maternity leave weeks"


def _client(cls):
    return cls(client_options=vertex_search.client_options(), transport=vertex_search.TRANSPORT)


def _parent() -> str:
    return (
        f"projects/{config.GOOGLE_CLOUD_PROJECT}/locations/{config.DATA_STORE_LOCATION}"
        "/collections/default_collection"
    )


def create_data_store() -> None:
    client = _client(de.DataStoreServiceClient)
    try:
        client.get_data_store(name=vertex_search.data_store_path())
        print(f"  = data store already exists: {config.DATA_STORE_ID}")
        return
    except exceptions.NotFound:
        pass
    print(f"  + creating data store: {config.DATA_STORE_ID} (takes ~1 min)")
    op = client.create_data_store(
        parent=_parent(),
        data_store_id=config.DATA_STORE_ID,
        data_store=de.DataStore(
            display_name="HR Policy Handbook (sections)",
            industry_vertical=de.IndustryVertical.GENERIC,
            solution_types=[de.SolutionType.SOLUTION_TYPE_SEARCH],
            content_config=de.DataStore.ContentConfig.NO_CONTENT,
        ),
    )
    op.result(timeout=600)


def build_documents() -> list[de.Document]:
    docs = []
    for i, chunk in enumerate(policy_vector_store.chunks):
        if not chunk.content.strip():
            continue  # section headers without body text
        docs.append(
            de.Document(
                id=f"sec-{i:03d}",
                struct_data={
                    "title": chunk.section_title,
                    "section_number": chunk.section_number,
                    "content": chunk.content,
                    "citation": chunk.citation,
                    "category": chunk.category,
                    "document": chunk.document_name,
                },
            )
        )
    return docs


def import_documents(docs: list[de.Document]) -> None:
    client = _client(de.DocumentServiceClient)
    branch = f"{vertex_search.data_store_path()}/branches/default_branch"
    print(f"  + importing {len(docs)} handbook sections")
    for start in range(0, len(docs), BATCH_SIZE):
        batch = docs[start : start + BATCH_SIZE]
        op = client.import_documents(
            request=de.ImportDocumentsRequest(
                parent=branch,
                inline_source=de.ImportDocumentsRequest.InlineSource(documents=batch),
                # Document IDs are stable (sec-NNN), so re-running overwrites in place.
                reconciliation_mode=de.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
            )
        )
        result = op.result(timeout=600)
        errors = list(result.error_samples)
        if errors:
            raise RuntimeError(f"import failed: {errors[0].message}")


def wait_until_searchable(timeout_s: int = 600) -> None:
    print("  … waiting for indexing (usually 1-5 min)", end="", flush=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = vertex_search.search(TEST_QUERY)
            if r["status"] == "SUCCESS":
                print(f"\n  ✓ search works: '{TEST_QUERY}' -> {r['primary_citation']}")
                return
        except exceptions.GoogleAPICallError:
            pass
        print(".", end="", flush=True)
        time.sleep(15)
    print("\n  ! not searchable yet. Wait a few minutes and run smoke_test.py again.")


def delete_data_store() -> None:
    client = _client(de.DataStoreServiceClient)
    try:
        client.delete_data_store(name=vertex_search.data_store_path()).result(timeout=600)
        print(f"  - deleted data store: {config.DATA_STORE_ID}")
    except exceptions.NotFound:
        print(f"  = data store not found: {config.DATA_STORE_ID}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--delete", action="store_true", help="delete the data store")
    args = parser.parse_args()

    if not config.GOOGLE_CLOUD_PROJECT or not config.DATA_STORE_ID:
        sys.exit("Set GOOGLE_CLOUD_PROJECT and DATA_STORE_ID in .env first.")
    print(
        f"\nProject: {config.GOOGLE_CLOUD_PROJECT}   Location: {config.DATA_STORE_LOCATION}"
        f"   Data store: {config.DATA_STORE_ID}\n"
    )

    if args.delete:
        delete_data_store()
        return

    create_data_store()
    import_documents(build_documents())
    wait_until_searchable()
    print("\nDone. Set this in .env to use the data store:\n\n  USE_VERTEX_SEARCH=true\n")


if __name__ == "__main__":
    try:
        main()
    except exceptions.PermissionDenied as e:
        sys.exit(
            f"\nPERMISSION_DENIED: {e.message}\n"
            "-> Enable the API:  gcloud services enable discoveryengine.googleapis.com\n"
            "-> Check your role: Discovery Engine Admin (roles/discoveryengine.admin)"
        )
