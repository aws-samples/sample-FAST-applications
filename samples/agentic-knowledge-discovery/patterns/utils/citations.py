# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Citation helper shared by both agent patterns.

Turns the document references the model cites into presigned URLs to the source
objects in the Knowledge Base documents bucket (DOCS_BUCKET), so the frontend can
link straight to the source. When a source is paginated the page number is added
as a URL fragment (#page=N) for PDF viewers.
"""

import os

import boto3

DOCS_BUCKET = os.environ.get("DOCS_BUCKET", "")
EXPIRY = int(os.environ.get("CITATION_URL_EXPIRY", "3600"))

_s3 = boto3.client("s3")


def _presign(doc_id: str) -> str:
    """Presign the source PDF (no page fragment — the frontend links to the
    first cited page)."""
    if not DOCS_BUCKET:
        return ""
    key = f"documents/{doc_id}.pdf"
    return _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": DOCS_BUCKET, "Key": key, "ResponseContentDisposition": "inline"},
        ExpiresIn=EXPIRY,
    )


def build_citations(sources) -> dict:
    """Build a citations payload from a list of {doc_id, page?} (or bare doc_id strings).

    Multiple cited chunks of the same document are merged into one citation whose
    ``pages`` is the sorted, de-duplicated list of pages. The frontend links to
    the first page in that list.
    """
    order: list[str] = []
    pages_by_doc: dict[str, set[int]] = {}
    for s in sources or []:
        doc_id = s.get("doc_id") if isinstance(s, dict) else s
        if not doc_id:
            continue
        if doc_id not in pages_by_doc:
            pages_by_doc[doc_id] = set()
            order.append(doc_id)
        page = s.get("page") if isinstance(s, dict) else None
        if page is not None:
            try:
                pages_by_doc[doc_id].add(int(page))
            except (TypeError, ValueError):
                pass

    out = [
        {"doc_id": doc_id, "pages": sorted(pages_by_doc[doc_id]), "url": _presign(doc_id)}
        for doc_id in order
    ]
    return {"response_type": "citations", "sources": out}
