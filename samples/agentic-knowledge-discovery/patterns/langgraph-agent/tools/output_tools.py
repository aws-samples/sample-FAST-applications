# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""LangGraph output tools: follow-up suggestions and source citations.

Lightweight in-runtime tools (no external I/O) that structure output the
frontend renders. Mirror the DISKOS agent's suggest_questions / cite_sources.
"""

import json
from typing import List

from langchain_core.tools import tool

from utils.citations import build_citations


@tool
def suggest_questions(questions: List[str]) -> str:
    """Suggest up to four relevant follow-up questions for the user to explore next.

    Call this once at the end of your answer. Pass short, specific questions the
    user is likely to ask next based on the current conversation.
    """
    return json.dumps({"response_type": "suggested_questions", "questions": questions[:4]})


@tool
def cite_sources(sources: List[dict]) -> str:
    """Record the sources you used, as a list of {"doc_id": "doc-002", "page": 3}.

    Pass the doc_id (and page, when the source is paginated) for each document
    returned by doc_search that your answer relied on. Each source is turned into
    a presigned URL to the original document, which the frontend shows as a
    clickable citation.
    """
    return json.dumps(build_citations(sources))
