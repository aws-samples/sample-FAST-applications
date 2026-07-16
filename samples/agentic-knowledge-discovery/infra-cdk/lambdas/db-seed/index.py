# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Database seed custom-resource handler.

Invoked by a CloudFormation custom resource (via the CDK Provider framework) on
stack Create/Update. Enables PostGIS, (re)creates the schema, and inserts a
small generic dataset. Delete is a no-op — the cluster is removed with the stack.
"""

import json
import os

import boto3
import psycopg2

DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
DB_CLUSTER_ENDPOINT = os.environ["DB_CLUSTER_ENDPOINT"]
DB_NAME = os.environ.get("DB_NAME", "ragmeta")

secrets_client = boto3.client("secretsmanager")


def _connect():
    creds = json.loads(
        secrets_client.get_secret_value(SecretId=DB_SECRET_ARN)["SecretString"]
    )
    return psycopg2.connect(
        host=DB_CLUSTER_ENDPOINT,
        port=creds.get("port", 5432),
        dbname=DB_NAME,
        user=creds["username"],
        password=creds["password"],
        connect_timeout=10,
    )


def _run_sql_file(cursor, filename: str) -> None:
    with open(
        os.path.join(os.path.dirname(__file__), filename), "r", encoding="utf-8"
    ) as fh:
        cursor.execute(fh.read())


def handler(event, context):
    request_type = event.get("RequestType", "Create")
    if request_type == "Delete":
        return {"PhysicalResourceId": "db-seed"}

    conn = _connect()
    try:
        conn.autocommit = False
        with conn.cursor() as cursor:
            _run_sql_file(cursor, "schema.sql")
            _run_sql_file(cursor, "seed.sql")
        conn.commit()
    finally:
        conn.close()

    return {"PhysicalResourceId": "db-seed", "Data": {"Seeded": "true"}}
