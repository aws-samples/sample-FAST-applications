# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""doc_search Gateway tool: batched hybrid retrieval over a Bedrock Knowledge Base.

Accepts one or more search requests in a single call and runs each against the
Knowledge Base with bedrock-agent-runtime Retrieve, applying an optional metadata
filter per request. Results are de-duplicated by chunk id across the batch so the
agent can issue several related queries at once without seeing repeats. This runs
as a Gateway Lambda target, so both the Strands and LangGraph agents call it
identically over MCP — the batch + dedup logic lives here, not in the runtime.

The Knowledge Base id comes from KNOWLEDGE_BASE_ID (set by the stack from the KB
it creates).
"""

import hashlib
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "").strip()
NUM_RESULTS_DEFAULT = 10
MAX_RESULTS = 25

_client = boto3.client("bedrock-agent-runtime")

VALID_FILTER_OPERATORS = {
    "equals",
    "greaterThan",
    "greaterThanOrEquals",
    "in",
    "lessThan",
    "lessThanOrEquals",
    "listContains",
    "notEquals",
    "notIn",
    "orAll",
    "andAll",
    "startsWith",
    "stringContains",
}


def _validate_filter(f: dict) -> None:
    if not isinstance(f, dict):
        raise ValueError("filter must be an object")
    for key, value in f.items():
        if key not in VALID_FILTER_OPERATORS:
            raise ValueError(f"invalid filter operator: {key}")
        if key in ("orAll", "andAll"):
            if not isinstance(value, list) or len(value) < 2:
                raise ValueError(f"'{key}' requires a list of at least 2 filters")
            for sub in value:
                _validate_filter(sub)


def _document_id(result: dict) -> str:
    loc = result.get("location", {})
    if "customDocumentLocation" in loc:
        return loc["customDocumentLocation"].get("id", "unknown")
    if "s3Location" in loc:
        return loc["s3Location"].get("uri", "unknown")
    return "unknown"


def _chunk_id(result: dict) -> str:
    cid = result.get("metadata", {}).get("x-amz-bedrock-kb-chunk-id")
    if cid:
        return cid
    # md5 is used only as a non-security content fingerprint for a chunk id.
    return hashlib.md5(
        result.get("content", {}).get("text", "").encode(), usedforsecurity=False
    ).hexdigest()


def _doc_id(result: dict) -> str:
    """Short document id (filename without extension) for cite_sources."""
    base = _document_id(result).rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


def _page(result: dict):
    """Page number for paginated sources (PDF), if the KB provides one."""
    return result.get("metadata", {}).get("x-amz-bedrock-kb-document-page-number")


def doc_search(
    search_requests: list, number_of_results: int = NUM_RESULTS_DEFAULT
) -> str:
    if not KNOWLEDGE_BASE_ID:
        return (
            "doc_search is not configured: no Knowledge Base id is set. The stack "
            "creates the Knowledge Base and wires its id automatically."
        )
    if not search_requests:
        return "Error: provide at least one search request with a query."

    n = max(1, min(int(number_of_results or NUM_RESULTS_DEFAULT), MAX_RESULTS))
    seen: set[str] = set()
    parts: list[str] = [f"Batch search: {len(search_requests)} query(ies)"]
    total_new = 0
    total_skipped = 0

    for i, req in enumerate(search_requests, 1):
        query = (req or {}).get("query", "")
        if not query.strip():
            continue
        vector_config = {"numberOfResults": n}
        req_filter = req.get("filter")
        if req_filter:
            try:
                _validate_filter(req_filter)
            except ValueError as exc:
                parts.append(f'\n## Query {i}: "{query}"\nInvalid filter: {exc}')
                continue
            vector_config["filter"] = req_filter

        response = _client.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": vector_config},
        )
        parts.append(f'\n## Query {i}: "{query}"')
        new_here = 0
        for r in response.get("retrievalResults", []):
            cid = _chunk_id(r)
            if cid in seen:
                total_skipped += 1
                continue
            seen.add(cid)
            new_here += 1
            score = r.get("score", 0.0)
            page = _page(r)
            page_str = f" page: {page}" if page else ""
            parts.append(
                f"\nScore: {score:.4f}\ndoc_id: {_doc_id(r)}{page_str}\n"
                f"Content: {r.get('content', {}).get('text', '')}"
            )
        total_new += new_here
        if new_here == 0:
            parts.append("\nNo new results.")

    summary = f"\n{total_new} unique result(s)"
    if total_skipped:
        summary += f" ({total_skipped} duplicate(s) skipped across queries)"
    parts.append(summary)
    return "\n".join(parts)


def handler(event, context):
    logger.info("doc_search event: %s", json.dumps(event))
    try:
        # Accept either a batch (search_requests) or a single query for convenience.
        search_requests = event.get("search_requests")
        if not search_requests and event.get("query"):
            search_requests = [
                {"query": event.get("query"), "filter": event.get("filter")}
            ]
        text = doc_search(
            search_requests or [], event.get("numberOfResults", NUM_RESULTS_DEFAULT)
        )
        return {"content": [{"type": "text", "text": text}]}
    except Exception as exc:  # noqa: BLE001
        logger.error("doc_search error: %s", exc)
        return {"error": f"Internal error: {exc}"}
