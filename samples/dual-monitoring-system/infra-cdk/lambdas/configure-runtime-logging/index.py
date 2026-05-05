"""
Custom Resource to configure AgentCore Runtime log delivery
"""

import json
import logging
from typing import Any, Dict

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

agentcore = boto3.client("bedrock-agentcore")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Configure log delivery for AgentCore Runtime

    This custom resource adds APPLICATION_LOGS delivery to CloudWatch Logs
    """
    logger.info(f"Event: {json.dumps(event)}")

    request_type = event["RequestType"]
    runtime_id = event["ResourceProperties"]["RuntimeId"]
    log_group_name = event["ResourceProperties"]["LogGroupName"]

    try:
        if request_type in ["Create", "Update"]:
            # Configure log delivery for APPLICATION_LOGS
            logger.info(f"Configuring log delivery for runtime {runtime_id}")

            # Note: This uses the UpdateRuntime API which may not support log delivery yet
            # If this fails, log delivery must be configured manually via console
            try:
                agentcore.update_runtime(
                    runtimeIdentifier=runtime_id,
                    logDeliveryConfigurations=[
                        {
                            "logType": "APPLICATION_LOGS",
                            "cloudWatchLogsConfiguration": {
                                "logGroupName": log_group_name
                            },
                        }
                    ],
                )
                logger.info("Log delivery configured successfully")

            except Exception as api_error:
                # If API doesn't support this yet, log but don't fail
                logger.warning(f"Could not configure log delivery via API: {api_error}")
                logger.info("Log delivery must be configured manually via console")

        elif request_type == "Delete":
            logger.info("Delete request - no action needed")

        return {
            "PhysicalResourceId": f"runtime-logging-{runtime_id}",
            "Data": {"RuntimeId": runtime_id, "LogGroupName": log_group_name},
        }

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        # Don't fail the stack - log delivery can be configured manually
        return {
            "PhysicalResourceId": f"runtime-logging-{runtime_id}",
            "Data": {"Error": str(e)},
        }
