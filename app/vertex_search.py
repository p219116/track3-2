# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Vertex AI Search (Discovery Engine) client for HR policy RAG.

The data store is created and loaded by `scripts/setup_vector_store.py`:
one document per handbook section, with struct_data
{title, section_number, content, citation, category, document}.

Unstructured (PDF) data stores, like the one provisioned in Lab 1, are also
supported: text is taken from the snippets Vertex AI Search returns.

Enabled with USE_VERTEX_SEARCH=true. `app.tools.search_hr_policy` falls back
to the local in-memory store if this call fails.
"""

from functools import lru_cache
from typing import Any

from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as de
from google.protobuf.json_format import MessageToDict

from app import config

_DISALLOWED_MARKERS = ("non-reimbursable", "prohibited", "disallowed", "forfeited")


def client_options() -> ClientOptions | None:
    """Regional data stores need a regional endpoint; `global` uses the default."""
    loc = config.DATA_STORE_LOCATION
    if loc and loc != "global":
        return ClientOptions(api_endpoint=f"{loc}-discoveryengine.googleapis.com")
    return None


# REST (HTTP/1.1) instead of gRPC: uses the OS resolver and certificate store,
# which works more reliably behind corporate VPNs and proxies.
TRANSPORT = "rest"


def data_store_path() -> str:
    return (
        f"projects/{config.GOOGLE_CLOUD_PROJECT}/locations/{config.DATA_STORE_LOCATION}"
        f"/collections/default_collection/dataStores/{config.DATA_STORE_ID}"
    )


@lru_cache(maxsize=1)
def _search_client() -> de.SearchServiceClient:
    return de.SearchServiceClient(client_options=client_options(), transport=TRANSPORT)


def _to_result(doc: de.Document) -> dict[str, Any] | None:
    data = MessageToDict(doc._pb.struct_data) if doc.struct_data else {}
    if data.get("content"):
        # Section-level document written by scripts/setup_vector_store.py
        content = str(data["content"])
        section = data.get("title", doc.id)
        document = data.get("document", "ALTOSTRAT_SG_Handbook.pdf")
        citation = data.get("citation", document)
    else:
        # Unstructured document (e.g. a PDF imported from Cloud Storage)
        derived = MessageToDict(doc._pb.derived_struct_data) if doc.derived_struct_data else {}
        snippets = [s.get("snippet", "") for s in derived.get("snippets", [])]
        content = " ".join(s for s in snippets if s).strip()
        section = derived.get("title", doc.id)
        document = citation = f"{section}.pdf"
    if not content:
        return None
    return {
        "document": document,
        "section": section,
        "content": content,
        "citation": citation,
        "is_disallowed": any(m in content.lower() for m in _DISALLOWED_MARKERS),
    }


def search(query: str, top_k: int = 3) -> dict[str, Any]:
    """Queries the Vertex AI Search data store. Raises on API errors."""
    request = de.SearchRequest(
        serving_config=f"{data_store_path()}/servingConfigs/default_search",
        query=query,
        page_size=top_k,
        query_expansion_spec=de.SearchRequest.QueryExpansionSpec(
            condition=de.SearchRequest.QueryExpansionSpec.Condition.AUTO
        ),
        spell_correction_spec=de.SearchRequest.SpellCorrectionSpec(
            mode=de.SearchRequest.SpellCorrectionSpec.Mode.AUTO
        ),
        content_search_spec=de.SearchRequest.ContentSearchSpec(
            snippet_spec=de.SearchRequest.ContentSearchSpec.SnippetSpec(return_snippet=True)
        ),
    )
    response = _search_client().search(request=request)
    results = [r for r in (_to_result(item.document) for item in response.results) if r]
    if not results:
        return {
            "status": "NOT_FOUND",
            "grounded": False,
            "message": "No official policy matching your query was found in the handbook.",
            "citation": None,
            "results": [],
            "source": "VERTEX_AI_SEARCH",
        }
    return {
        "status": "SUCCESS",
        "grounded": True,
        "results": results[:top_k],
        "primary_citation": results[0]["citation"],
        "source": "VERTEX_AI_SEARCH",
    }
