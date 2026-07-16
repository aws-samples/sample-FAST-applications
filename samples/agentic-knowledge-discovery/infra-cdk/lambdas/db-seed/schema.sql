-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: Apache-2.0
--
-- Generic structured metadata for the document corpus. One row per document,
-- with attributes the agent can filter and aggregate on. The same attributes
-- are attached to each document in the Knowledge Base (metadata sidecars), and
-- doc_id links a metadata row to its document, so the agent can pivot between
-- structured (metadata_search) and unstructured (doc_search) retrieval.

DROP TABLE IF EXISTS documents;

CREATE TABLE documents (
    doc_id     TEXT PRIMARY KEY,
    domain     TEXT NOT NULL,
    doc_type   TEXT NOT NULL,
    num_pages  INTEGER NOT NULL,
    title      TEXT
);

CREATE INDEX documents_domain_idx ON documents (domain);
CREATE INDEX documents_doc_type_idx ON documents (doc_type);
