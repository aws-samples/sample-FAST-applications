# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import os

import boto3
from aws_lambda_powertools import Logger

logger = Logger()
bedrock_agent = boto3.client("bedrock-agent")


@logger.inject_lambda_context
def handler(event, context):
    """Trigger Knowledge Base sync."""
    try:
        kb_id = os.environ["KNOWLEDGE_BASE_ID"]
        ds_id = os.environ["DATA_SOURCE_ID"]

        response = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id
        )

        return {
            "statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps(
                {
                    "message": "Knowledge Base sync started",
                    "jobId": response["ingestionJob"]["ingestionJobId"],
                }
            ),
        }
    except Exception as e:
        logger.exception("Error starting KB sync")
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(e)}),
        }
