# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import os

import boto3
from aws_lambda_powertools import Logger
from botocore.config import Config

logger = Logger()
s3_client = boto3.client("s3", config=Config(signature_version="s3v4"))


@logger.inject_lambda_context
def handler(event, context):
    """Generate presigned URL for S3 file upload."""
    try:
        body = json.loads(event.get("body", "{}"))
        filename = body.get("filename")

        if not filename:
            return {
                "statusCode": 400,
                "headers": {"Access-Control-Allow-Origin": "*"},
                "body": json.dumps({"error": "filename is required"}),
            }

        bucket = os.environ["KB_BUCKET_NAME"]
        key = f"uploads/{filename}"

        presigned_url = s3_client.generate_presigned_url(
            "put_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
        )

        return {
            "statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"uploadUrl": presigned_url, "key": key}),
        }
    except Exception as e:
        logger.exception("Error generating presigned URL")
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(e)}),
        }
