# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""structured_search Gateway tool: read-only structured queries over Aurora PostgreSQL.

Two tools:
- describe_schema: list tables and columns so the agent can plan a query.
- run_sql_query:  execute a single read-only (SELECT) statement and return rows as text.

SELECT-only is enforced with sqlglot (falls back to a keyword check). This tool
does no per-user authorization in v1; the Gateway enforces authentication.
"""

import json
import logging
import os
import time

import boto3
import psycopg2

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
DB_CLUSTER_ENDPOINT = os.environ["DB_CLUSTER_ENDPOINT"]
DB_NAME = os.environ.get("DB_NAME", "ragmeta")
QUERY_TIMEOUT_S = 30
MAX_ROWS = 100

secrets_client = boto3.client("secretsmanager")
_creds_cache = {"value": None, "ts": 0.0}


def _get_credentials():
    now = time.time()
    if _creds_cache["value"] and now - _creds_cache["ts"] < 300:
        return _creds_cache["value"]
    secret = secrets_client.get_secret_value(SecretId=DB_SECRET_ARN)["SecretString"]
    _creds_cache["value"] = json.loads(secret)
    _creds_cache["ts"] = now
    return _creds_cache["value"]


def _connect():
    creds = _get_credentials()
    return psycopg2.connect(
        host=DB_CLUSTER_ENDPOINT,
        port=creds.get("port", 5432),
        dbname=DB_NAME,
        user=creds["username"],
        password=creds["password"],
        connect_timeout=10,
    )


def _is_select_only(query: str) -> bool:
    """True only if the statement is a single SELECT (or WITH ... SELECT)."""
    try:
        from sqlglot import parse
        from sqlglot.expressions import Select

        statements = parse(query, dialect="postgres")
        if len(statements) != 1 or statements[0] is None:
            return False
        stmt = statements[0]
        return isinstance(stmt, Select) or (stmt.key == "select")
    except Exception:
        q = query.strip().lstrip("(").upper()
        if not (q.startswith("SELECT") or q.startswith("WITH")):
            return False
        import re

        return not re.search(
            r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b", q
        )


def run_sql_query(query: str, limit: int = MAX_ROWS) -> str:
    if not query or not query.strip():
        return "Error: empty query."
    if not _is_select_only(query):
        return "Error: only a single read-only SELECT statement is allowed."

    limit = max(1, min(int(limit or MAX_ROWS), MAX_ROWS))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {QUERY_TIMEOUT_S * 1000}")
            # nosec B608 - text-to-SQL tool by design: the statement is validated as a
            # single read-only SELECT by _is_select_only() (sqlglot), the LIMIT is a
            # sanitized integer, and a statement_timeout bounds execution.
            wrapped = f"SELECT * FROM ({query.rstrip(';')}) AS _q LIMIT {limit}"  # nosec B608
            cur.execute(wrapped)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
    except psycopg2.Error as exc:
        return f"Query error: {exc}"
    finally:
        conn.close()

    if not rows:
        return "Query returned 0 rows."
    header = " | ".join(columns)
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    return f"{len(rows)} row(s):\n" + "\n".join(lines)


def describe_schema() -> str:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
            rows = cur.fetchall()
    except psycopg2.Error as exc:
        return f"Schema error: {exc}"
    finally:
        conn.close()

    tables: dict[str, list[str]] = {}
    for table_name, column_name, data_type in rows:
        tables.setdefault(table_name, []).append(f"{column_name} {data_type}")
    if not tables:
        return "No tables found in the public schema."
    out = ["Schema (public):"]
    for table_name, cols in tables.items():
        out.append(f"\n{table_name}:")
        out.extend(f"  - {c}" for c in cols)
    return "\n".join(out)


def handler(event, context):
    logger.info("structured_search event: %s", json.dumps(event))
    try:
        original = context.client_context.custom["bedrockAgentCoreToolName"]
        tool_name = (
            original[original.index("___") + 3 :] if "___" in original else original
        )

        if tool_name == "describe_schema":
            text = describe_schema()
        elif tool_name == "run_sql_query":
            text = run_sql_query(event.get("query", ""), event.get("limit", MAX_ROWS))
        else:
            return {"error": f"Unsupported tool: {tool_name}"}

        return {"content": [{"type": "text", "text": text}]}
    except Exception as exc:  # noqa: BLE001
        logger.error("structured_search error: %s", exc)
        return {"error": f"Internal error: {exc}"}
