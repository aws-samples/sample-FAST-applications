# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Evaluation API Lambda Handler"""

import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import json

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, CORSConfig
from aws_lambda_powertools.logging.correlation_paths import API_GATEWAY_REST
from aws_lambda_powertools.utilities.typing import LambdaContext

from cloudwatch_client import CloudWatchClient
from agentcore_evaluator import AgentCoreEvaluator
from models import Session, Trace, EvaluationResult, SessionStatus, Span
from ai_engine import AIAnalysisEngine

# Environment variables
CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "*")

# Parse CORS origins - can be comma-separated list
cors_origins = [
    origin.strip() for origin in CORS_ALLOWED_ORIGINS.split(",") if origin.strip()
]
primary_origin = cors_origins[0] if cors_origins else "*"
extra_origins = cors_origins[1:] if len(cors_origins) > 1 else None


# Configure CORS
cors_config = CORSConfig(
    allow_origin=primary_origin,
    extra_origins=extra_origins,
    allow_headers=["Content-Type", "Authorization", "Cache-Control", "Pragma",  "X-Request-ID"],
    allow_credentials=True,
)

# Initialize clients
cloudwatch_logs = boto3.client("logs")
bedrock_runtime = boto3.client("bedrock-runtime")
cw_client = CloudWatchClient()
agentcore_eval = AgentCoreEvaluator()  # Now uses SDK

tracer = Tracer()
logger = Logger()
app = APIGatewayRestResolver(cors=cors_config)


def _parse_datetime(date_str: Optional[str], default: datetime) -> datetime:
    """
    Parse ISO8601 datetime string or return default
    
    Args:
        date_str: ISO8601 datetime string
        default: Default datetime if parsing fails
        
    Returns:
        Parsed datetime or default
    """
    if not date_str:
        return default
    
    try:
        # Handle ISO8601 with Z suffix
        if date_str.endswith("Z"):
            date_str = date_str[:-1] + "+00:00"
        return datetime.fromisoformat(date_str)
    except (ValueError, AttributeError):
        logger.warning(f"Failed to parse datetime: {date_str}")
        return default


def _parse_float(value: Optional[str], default: Optional[float] = None) -> Optional[float]:
    """
    Parse float from string or return default
    
    Args:
        value: String value to parse
        default: Default value if parsing fails
        
    Returns:
        Parsed float or default
    """
    if value is None:
        return default
    
    try:
        return float(value)
    except (ValueError, TypeError):
        logger.warning(f"Failed to parse float: {value}")
        return default


def _parse_int(value: Optional[str], default: int) -> int:
    """
    Parse int from string or return default
    
    Args:
        value: String value to parse
        default: Default value if parsing fails
        
    Returns:
        Parsed int or default
    """
    if value is None:
        return default
    
    try:
        return int(value)
    except (ValueError, TypeError):
        logger.warning(f"Failed to parse int: {value}")
        return default


def _build_session_from_records(
    span_records: List[Dict[str, Any]],
    eval_records: List[Dict[str, Any]]
) -> Dict[str, Session]:
    """
    Build Session objects from CloudWatch records
    
    Args:
        span_records: List of span records from CloudWatch
        eval_records: List of evaluation records from CloudWatch
        
    Returns:
        Dictionary mapping session_id to Session object
    """
    # Group spans by session and trace
    sessions_data: Dict[str, Dict[str, Any]] = {}
    
    # Process spans
    for record in span_records:
        try:
            span = cw_client.parse_otel_span(record)
            session_id = record.get("sessionId", "unknown")
            
            if session_id not in sessions_data:
                sessions_data[session_id] = {
                    "spans": [],
                    "timestamp": span.start_time
                }
            
            sessions_data[session_id]["spans"].append(span)
            
            # Update session timestamp to earliest span
            if span.start_time < sessions_data[session_id]["timestamp"]:
                sessions_data[session_id]["timestamp"] = span.start_time
                
        except Exception as e:
            logger.warning(f"Failed to parse span record: {e}")
            continue
    
    # Process evaluations
    evaluations: Dict[str, EvaluationResult] = {}
    for record in eval_records:
        try:
            session_id = record.get("sessionId", "")
            if not session_id:
                continue
            
            evaluation = EvaluationResult(
                evaluation_id=record.get("evaluationId", ""),
                session_id=session_id,
                score=float(record.get("score", 0.0)),
                criteria=record.get("criteria", {}),
                feedback=record.get("feedback"),
                timestamp=_parse_datetime(record.get("timestamp"), datetime.utcnow())
            )
            evaluations[session_id] = evaluation
            
        except Exception as e:
            logger.warning(f"Failed to parse evaluation record: {e}")
            continue
    
    # Build Session objects
    sessions: Dict[str, Session] = {}
    for session_id, data in sessions_data.items():
        # Group spans by trace
        traces_dict: Dict[str, List[Span]] = {}
        for span in data["spans"]:
            if span.trace_id not in traces_dict:
                traces_dict[span.trace_id] = []
            traces_dict[span.trace_id].append(span)
        
        # Create Trace objects
        traces = []
        for trace_id, spans in traces_dict.items():
            if not spans:
                continue
            
            # Sort spans by start time
            spans.sort(key=lambda s: s.start_time)
            
            trace = Trace(
                trace_id=trace_id,
                spans=spans,
                start_time=spans[0].start_time,
                end_time=spans[-1].end_time
            )
            traces.append(trace)
        
        # Sort traces by start time
        traces.sort(key=lambda t: t.start_time)
        
        # Determine session status
        status = SessionStatus.COMPLETED
        if session_id in evaluations:
            # Check if any spans have error status
            has_errors = any(span.status != "OK" for span in data["spans"])
            if has_errors:
                status = SessionStatus.FAILED
        
        session = Session(
            session_id=session_id,
            timestamp=data["timestamp"],
            traces=traces,
            evaluation=evaluations.get(session_id),
            status=status,
            metadata={}
        )
        sessions[session_id] = session
    
    return sessions



@app.get("/evaluations/sessions")
@tracer.capture_method
def list_sessions() -> Dict[str, Any]:
    """
    List runtime sessions from CloudWatch

    Query parameters:
        start_date: ISO8601 datetime (default: 2 days ago)
        end_date: ISO8601 datetime (default: now)
        limit: Maximum number of sessions to return (default: 100)

    Returns:
        Session list with metadata
    """
    try:
        # Parse query parameters
        params = app.current_event.query_string_parameters or {}
        
        logger.info(f"Raw query parameters: {params}")

        # Time range (default to last 7 days)
        end_time = _parse_datetime(params.get("end_date"), datetime.utcnow())
        start_time = _parse_datetime(
            params.get("start_date"),
            end_time - timedelta(days=3)
        )

        # Pagination
        limit = _parse_int(params.get("limit"), 100)

        logger.info(f"Parsed parameters:")
        logger.info(f"  start_time: {start_time} (from param: {params.get('start_date')})")
        logger.info(f"  end_time: {end_time} (from param: {params.get('end_date')})")
        logger.info(f"  limit: {limit}")
        logger.info(f"Listing sessions from {start_time} to {end_time}")

        # Query sessions from runtime logs
        sessions = cw_client.query_sessions(
            start_time=start_time,
            end_time=end_time,
            min_score=None,
            max_score=None,
            limit=limit
        )

        # Sort by timestamp (most recent first)
        sessions.sort(key=lambda s: s.timestamp, reverse=True)

        # Calculate statistics
        total_sessions = len(sessions)

        # Build response
        response = {
            "sessions": [
                {
                    "sessionId": s.session_id,
                    "timestamp": s.timestamp.isoformat(),
                    "score": s.score,
                    "traceCount": s.trace_count,
                    "spanCount": s.span_count,
                    "status": s.status.value
                }
                for s in sessions
            ],
            "nextToken": None,
            "statistics": {
                "totalSessions": total_sessions,
                "averageScore": 0.0
            }
        }
        
        logger.info(f"Returning {len(response['sessions'])} sessions")
        if response['sessions']:
            logger.info(f"First session timestamp: {response['sessions'][0]['timestamp']}")
            logger.info(f"Last session timestamp: {response['sessions'][-1]['timestamp']}")

        return response

    except Exception as e:
        logger.error(f"Error listing sessions: {str(e)}", exc_info=True)
        return {"error": "Failed to list sessions"}, 500


@app.get("/evaluations/sessions/<session_id>")
@tracer.capture_method
def get_session(session_id: str) -> Dict[str, Any]:
    """
    Get detailed session data including all traces and spans using Observability SDK

    Path parameters:
        session_id: Session ID to retrieve

    Returns:
        Complete session data with traces and spans
    """
    try:

        # Get agent ID from runtime ARN
        runtime_arn = os.environ.get('RUNTIME_ARN', '')
        agent_id = ''
        
        if runtime_arn:
            agent_id = runtime_arn.split('/')[-1] if runtime_arn else ''
            logger.info(f"Using agent_id from RUNTIME_ARN: {agent_id}")
        else:
            logger.warning("RUNTIME_ARN not set, will try to infer agent_id from session data")
        
        # Use Observability SDK to get session data
        from bedrock_agentcore_starter_toolkit import Observability
        
        obs_client = Observability(agent_id=agent_id, region=os.environ.get('AWS_REGION', 'us-east-1'))
        
        # Try to get session traces
        # If agent_id is not available, the SDK might be able to query without it
        try:
            if agent_id:
                trace_data = obs_client.list(
                    session_id=session_id
                )
            else:
                # Fallback: Try without agent_id (might not work with all SDK versions)
                # If this fails, we'll fall back to CloudWatch client
                trace_data = obs_client.get_session_traces(
                    session_id=session_id
                )
        except Exception as sdk_error:
            logger.warning(f"Observability SDK failed: {sdk_error}, falling back to CloudWatch client")
            # Fallback to CloudWatch client
            session = cw_client.get_session_detail(session_id)
            
            if not session:
                logger.warning(f"Session {session_id} not found in CloudWatch logs")
                return {"error": "Session not found"}, 404
            
            return session.to_dict()
        
        if not trace_data or not trace_data.traces:
            logger.warning(f"Session {session_id} not found in AgentCore Observability")
            return {"error": "Session not found"}, 404

        logger.info(f"Session found via SDK: {len(list(trace_data.traces.keys()))} traces, {len(trace_data.spans)} spans")
        
        # Format TraceData to match frontend expectations
        formatted_traces = []
        for trace_id, trace_spans in trace_data.traces.items():
            if not trace_spans:
                continue
            
            # Sort spans by start time
            trace_spans.sort(key=lambda s: s.start_time_unix_nano or 0)
            
            formatted_trace = {
                'traceId': trace_id,
                'spans': [
                    {
                        'spanId': span.span_id,
                        'traceId': span.trace_id,
                        'parentSpanId': span.parent_span_id,
                        'name': span.span_name,
                        'startTime': datetime.fromtimestamp(span.start_time_unix_nano / 1_000_000_000).isoformat() if span.start_time_unix_nano else None,
                        'endTime': datetime.fromtimestamp(span.end_time_unix_nano / 1_000_000_000).isoformat() if span.end_time_unix_nano else None,
                        'durationMs': span.duration_ms,
                        'status': span.status_code or 'UNSET',
                        'attributes': span.attributes or {}
                    }
                    for span in trace_spans
                ],
                'startTime': datetime.fromtimestamp(trace_spans[0].start_time_unix_nano / 1_000_000_000).isoformat() if trace_spans[0].start_time_unix_nano else None,
                'endTime': datetime.fromtimestamp(trace_spans[-1].end_time_unix_nano / 1_000_000_000).isoformat() if trace_spans[-1].end_time_unix_nano else None,
                'durationMs': sum(s.duration_ms or 0 for s in trace_spans)
            }
            formatted_traces.append(formatted_trace)
        
        # Format response
        return {
            "sessionId": session_id,
            "timestamp": datetime.fromtimestamp(trace_data.start_time / 1_000_000_000).isoformat() if trace_data.start_time else datetime.utcnow().isoformat(),
            "traces": formatted_traces,
            "traceCount": len(formatted_traces),
            "spanCount": len(trace_data.spans),
            "status": "completed",
            "metadata": {}
        }

    except Exception as e:
        logger.error(f"Error retrieving session {session_id}: {str(e)}", exc_info=True)
        return {"error": f"Failed to retrieve session: {str(e)}"}, 500


def process_analysis_job(job_id: str, score_threshold: float, limit: int) -> None:
    """
    Background processor for analysis jobs
    
    Args:
        job_id: Analysis job ID
        score_threshold: Score threshold for filtering
        limit: Maximum number of results to analyze
    """
    from decimal import Decimal
    
    def convert_floats_to_decimal(obj):
        """Recursively convert floats to Decimal for DynamoDB"""
        if isinstance(obj, float):
            return Decimal(str(obj))
        elif isinstance(obj, dict):
            return {k: convert_floats_to_decimal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_floats_to_decimal(item) for item in obj]
        return obj
    
    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ.get('ANALYSIS_JOBS_TABLE', 'evaluation-analysis-jobs')
    table = dynamodb.Table(table_name)
    
    try:
        # Update status to PROCESSING
        table.update_item(
            Key={'jobId': job_id},
            UpdateExpression='SET #status = :status, updatedAt = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'PROCESSING',
                ':updated': datetime.utcnow().isoformat()
            }
        )
        
        logger.info(f"Processing analysis job {job_id}")
        
        # Query for evaluation results (last 30 days)
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=30)
        
        # Get all evaluation configs
        configs = agentcore_eval.list_online_evaluations()
        
        if not configs:
            raise ValueError("No evaluation configurations found")
        
        # Collect evaluation results from all configs
        all_evaluation_results = []
        
        for config in configs:
            config_id = config.get('onlineEvaluationConfigId')
            if not config_id:
                continue
            
            try:
                cloudwatch_logs = boto3.client('logs')
                log_group = f"/aws/bedrock-agentcore/evaluations/results/{config_id}"
                
                start_ms = int(start_time.timestamp() * 1000)
                end_ms = int(end_time.timestamp() * 1000)
                
                next_token = None
                while True:
                    params = {
                        'logGroupName': log_group,
                        'startTime': start_ms,
                        'endTime': end_ms,
                        'limit': 10000
                    }
                    
                    if next_token:
                        params['nextToken'] = next_token
                    
                    response = cloudwatch_logs.filter_log_events(**params)
                    
                    for event in response.get('events', []):
                        try:
                            log_data = json.loads(event['message'])
                            all_evaluation_results.append(log_data)
                        except json.JSONDecodeError:
                            continue
                    
                    next_token = response.get('nextToken')
                    if not next_token:
                        break
                
            except Exception as e:
                logger.warning(f"Failed to load evaluation results from config {config_id}: {e}")
                continue
        
        if not all_evaluation_results:
            result = {
                "analysisId": str(uuid.uuid4()),
                "patterns": [],
                "summary": "No evaluation results found",
                "recommendations": [],
                "timestamp": datetime.utcnow().isoformat()
            }
        else:
            # Filter for low-scoring evaluations
            low_scoring_results = [
                result for result in all_evaluation_results
                if result.get('attributes', {}).get('gen_ai.evaluation.score.value', 1.0) <= score_threshold
            ]
            
            logger.info(f"Found {len(low_scoring_results)} low-scoring evaluations")
            
            if not low_scoring_results:
                result = {
                    "analysisId": str(uuid.uuid4()),
                    "patterns": [],
                    "summary": f"No evaluations found with score <= {score_threshold}",
                    "recommendations": [],
                    "timestamp": datetime.utcnow().isoformat()
                }
            else:
                # Run AI analysis
                ai_engine = AIAnalysisEngine(bedrock_runtime)
                analysis_result = ai_engine.analyze_evaluation_results(low_scoring_results[:limit])
                result = analysis_result.to_dict()
        
        # Update job with results (convert floats to Decimal)
        table.update_item(
            Key={'jobId': job_id},
            UpdateExpression='SET #status = :status, #result = :result, updatedAt = :updated',
            ExpressionAttributeNames={
                '#status': 'status',
                '#result': 'result'
            },
            ExpressionAttributeValues={
                ':status': 'COMPLETED',
                ':result': convert_floats_to_decimal(result),
                ':updated': datetime.utcnow().isoformat()
            }
        )
        
        logger.info(f"Analysis job {job_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Analysis job {job_id} failed: {str(e)}", exc_info=True)
        
        # Update job with error
        try:
            table.update_item(
                Key={'jobId': job_id},
                UpdateExpression='SET #status = :status, #error = :error, updatedAt = :updated',
                ExpressionAttributeNames={
                    '#status': 'status',
                    '#error': 'error'
                },
                ExpressionAttributeValues={
                    ':status': 'FAILED',
                    ':error': str(e),
                    ':updated': datetime.utcnow().isoformat()
                }
            )
        except Exception as update_error:
            logger.error(f"Failed to update job status: {update_error}")




def process_prompt_improvement_job(
    job_id: str,
    current_prompt: str,
    analysis_id: Optional[str],
    score_threshold: float,
    limit: int
) -> None:
    """
    Background processor for prompt improvement jobs
    
    Args:
        job_id: Prompt improvement job ID
        current_prompt: Current system prompt text
        analysis_id: Optional analysis ID to use
        score_threshold: Score threshold for filtering
        limit: Maximum number of sessions to analyze
    """
    from decimal import Decimal
    
    def convert_floats_to_decimal(obj):
        """Recursively convert floats to Decimal for DynamoDB"""
        if isinstance(obj, float):
            return Decimal(str(obj))
        elif isinstance(obj, dict):
            return {k: convert_floats_to_decimal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_floats_to_decimal(item) for item in obj]
        return obj
    
    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ.get('ANALYSIS_JOBS_TABLE', 'evaluation-analysis-jobs')
    table = dynamodb.Table(table_name)
    
    try:
        # Update status to PROCESSING
        table.update_item(
            Key={'jobId': job_id},
            UpdateExpression='SET #status = :status, updatedAt = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'PROCESSING',
                ':updated': datetime.utcnow().isoformat()
            }
        )
        
        logger.info(f"Processing prompt improvement job {job_id}")
        
        # If no analysisId provided, run analysis first
        if not analysis_id:
            logger.info("No analysisId provided, running analysis first")
            
            # Query for low-scoring sessions (last 30 days)
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=30)
            
            # Query sessions from CloudWatch
            sessions = cw_client.query_sessions(
                start_time=start_time,
                end_time=end_time,
                min_score=None,
                max_score=None,
                limit=limit
            )
            
            # Since we don't have evaluation scores, analyze all sessions
            low_scoring_sessions = sessions[:limit]
            
            if not low_scoring_sessions:
                raise ValueError(f"No sessions found with score <= {score_threshold} to base improvements on")
            
            logger.info(f"Found {len(low_scoring_sessions)} low-scoring sessions for analysis")
            
            # Initialize AI engine and run analysis
            ai_engine = AIAnalysisEngine(bedrock_runtime)
            analysis_result = ai_engine.analyze_patterns(low_scoring_sessions)
        else:
            # TODO: Retrieve stored analysis by ID
            raise NotImplementedError("Using stored analysisId is not yet implemented")
        
        # Generate prompt improvements based on analysis
        ai_engine = AIAnalysisEngine(bedrock_runtime)
        improvement = ai_engine.generate_prompt_improvements(
            current_prompt=current_prompt,
            analysis=analysis_result
        )
        
        # Convert result to dict
        result = improvement.to_dict()
        
        # Update job with results (convert floats to Decimal)
        table.update_item(
            Key={'jobId': job_id},
            UpdateExpression='SET #status = :status, #result = :result, updatedAt = :updated',
            ExpressionAttributeNames={
                '#status': 'status',
                '#result': 'result'
            },
            ExpressionAttributeValues={
                ':status': 'COMPLETED',
                ':result': convert_floats_to_decimal(result),
                ':updated': datetime.utcnow().isoformat()
            }
        )
        
        logger.info(f"Prompt improvement job {job_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Prompt improvement job {job_id} failed: {str(e)}", exc_info=True)
        
        # Update job with error
        try:
            table.update_item(
                Key={'jobId': job_id},
                UpdateExpression='SET #status = :status, #error = :error, updatedAt = :updated',
                ExpressionAttributeNames={
                    '#status': 'status',
                    '#error': 'error'
                },
                ExpressionAttributeValues={
                    ':status': 'FAILED',
                    ':error': str(e),
                    ':updated': datetime.utcnow().isoformat()
                }
            )
        except Exception as update_error:
            logger.error(f"Failed to update job status: {update_error}")


@app.post("/evaluations/analyze")
@tracer.capture_method
def analyze_sessions() -> Dict[str, Any]:
    """
    Trigger AI analysis of low-scoring sessions (Async)
    
    Request body:
        scoreThreshold: Maximum score to include in analysis (0.0-1.0)
        limit: Maximum number of sessions to analyze (default: 100)
        
    Returns:
        Job ID for polling status
    """
    try:
        # Parse request body
        body = app.current_event.json_body
        
        if not body:
            return {"error": "Request body is required"}, 400
        
        # Extract parameters
        score_threshold = body.get("scoreThreshold", 0.5)
        limit = body.get("limit", 100)
        
        # Validate parameters
        if not isinstance(score_threshold, (int, float)) or not 0 <= score_threshold <= 1:
            return {"error": "scoreThreshold must be a number between 0 and 1"}, 400
        
        if not isinstance(limit, int) or limit < 1 or limit > 1000:
            return {"error": "limit must be an integer between 1 and 1000"}, 400
        
        # Generate job ID
        job_id = str(uuid.uuid4())
        
        logger.info(f"Starting async analysis job: {job_id}")
        
        # Store job in DynamoDB with PENDING status
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get('ANALYSIS_JOBS_TABLE', 'evaluation-analysis-jobs')
        table = dynamodb.Table(table_name)
        
        from decimal import Decimal
        
        table.put_item(
            Item={
                'jobId': job_id,
                'status': 'PENDING',
                'scoreThreshold': Decimal(str(score_threshold)),
                'limit': limit,
                'createdAt': datetime.utcnow().isoformat(),
                'updatedAt': datetime.utcnow().isoformat()
            }
        )
        
        # Invoke Lambda asynchronously to process analysis
        lambda_client = boto3.client('lambda')
        function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME')
        
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',  # Async invocation
            Payload=json.dumps({
                'action': 'process_analysis',
                'jobId': job_id,
                'scoreThreshold': score_threshold,
                'limit': limit
            })
        )
        
        logger.info(f"Analysis job {job_id} queued for processing")
        
        return {
            "jobId": job_id,
            "status": "PENDING",
            "message": "Analysis job started. Use the jobId to poll for results."
        }
        
    except Exception as e:
        logger.error(f"Error starting analysis: {str(e)}", exc_info=True)
        return {"error": "Failed to start analysis"}, 500


@app.get("/evaluations/analyze/<job_id>")
@tracer.capture_method
def get_analysis_status(job_id: str) -> Dict[str, Any]:
    """
    Get status and results of an analysis job
    
    Path parameters:
        job_id: Analysis job ID
        
    Returns:
        Job status and results (if complete)
    """
    from decimal import Decimal
    
    def convert_decimals(obj):
        """Recursively convert Decimal to float for JSON serialization"""
        if isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, dict):
            return {k: convert_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_decimals(item) for item in obj]
        return obj
    
    try:
        logger.info(f"Getting analysis job status: {job_id}")
        
        # Get job from DynamoDB
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get('ANALYSIS_JOBS_TABLE', 'evaluation-analysis-jobs')
        table = dynamodb.Table(table_name)
        
        response = table.get_item(Key={'jobId': job_id})
        
        if 'Item' not in response:
            return {"error": "Job not found"}, 404
        
        job = response['Item']
        
        logger.info(f"Job status: {job.get('status')}")
        
        # Return job status and results (convert Decimals to floats)
        result = {
            "jobId": job_id,
            "status": job.get('status'),
            "createdAt": job.get('createdAt'),
            "updatedAt": job.get('updatedAt')
        }
        
        # Include results if complete
        if job.get('status') == 'COMPLETED' and 'result' in job:
            result['result'] = convert_decimals(job['result'])
            logger.info(f"Returning completed job with {len(result['result'].get('patterns', []))} patterns")
        
        # Include error if failed
        if job.get('status') == 'FAILED' and 'error' in job:
            result['error'] = job['error']
        
        return result
        
    except Exception as e:
        logger.error(f"Error getting analysis status: {str(e)}", exc_info=True)
        return {"error": "Failed to get analysis status"}, 500


@app.post("/evaluations/improve-prompt")
@tracer.capture_method
def improve_prompt() -> Dict[str, Any]:
    """
    Generate prompt improvements based on analysis (Async)
    
    Request body:
        currentPrompt: Current system prompt text
        analysisId: (Optional) ID of previous analysis to use
        scoreThreshold: (Optional) Score threshold for analysis if no analysisId provided
        limit: (Optional) Session limit for analysis if no analysisId provided
        
    Returns:
        Job ID for polling status
    """
    try:
        logger.info("improve_prompt - creating async job")
        # Parse request body
        body = app.current_event.json_body
        
        if not body:
            return {"error": "Request body is required"}, 400
        
        # Extract parameters
        current_prompt = body.get("currentPrompt", "")
        analysis_id = body.get("analysisId")
        score_threshold = body.get("scoreThreshold", 0.5)
        limit = body.get("limit", 100)
        
        # Validate current prompt
        if not current_prompt or not isinstance(current_prompt, str):
            return {"error": "currentPrompt is required and must be a string"}, 400
        
        # Validate parameters if no analysisId
        if not analysis_id:
            if not isinstance(score_threshold, (int, float)) or not 0 <= score_threshold <= 1:
                return {"error": "scoreThreshold must be a number between 0 and 1"}, 400
            
            if not isinstance(limit, int) or limit < 1 or limit > 1000:
                return {"error": "limit must be an integer between 1 and 1000"}, 400
        
        # Create job in DynamoDB
        job_id = str(uuid.uuid4())
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get('ANALYSIS_JOBS_TABLE', 'evaluation-analysis-jobs')
        table = dynamodb.Table(table_name)
        
        from decimal import Decimal
        
        job_item = {
            'jobId': job_id,
            'jobType': 'PROMPT_IMPROVEMENT',
            'status': 'PENDING',
            'createdAt': datetime.utcnow().isoformat(),
            'updatedAt': datetime.utcnow().isoformat(),
            'parameters': {
                'currentPrompt': current_prompt,
                'analysisId': analysis_id,
                'scoreThreshold': Decimal(str(score_threshold)),
                'limit': limit
            }
        }
        
        table.put_item(Item=job_item)
        
        logger.info(f"Created prompt improvement job: {job_id}")
        
        # Invoke Lambda asynchronously to process the job
        lambda_client = boto3.client('lambda')
        function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME')
        
        payload = {
            'action': 'process_prompt_improvement',
            'jobId': job_id,
            'currentPrompt': current_prompt,
            'analysisId': analysis_id,
            'scoreThreshold': score_threshold,
            'limit': limit
        }
        
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',  # Async invocation
            Payload=json.dumps(payload)
        )
        
        logger.info(f"Triggered async processing for job {job_id}")
        
        return {
            "jobId": job_id,
            "status": "PENDING",
            "message": "Prompt improvement job created successfully"
        }
        
    except Exception as e:
        logger.error(f"Error creating prompt improvement job: {str(e)}", exc_info=True)
        return {"error": "Failed to create prompt improvement job"}, 500


@app.get("/evaluations/improve-prompt/status/<job_id>")
@tracer.capture_method
def get_prompt_improvement_status(job_id: str) -> Dict[str, Any]:
    """
    Get status of prompt improvement job
    
    Path parameters:
        job_id: Job ID returned from improve-prompt endpoint
        
    Returns:
        Job status and results if complete
    """
    from decimal import Decimal
    
    def convert_decimals(obj):
        """Recursively convert Decimal to float for JSON serialization"""
        if isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, dict):
            return {k: convert_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_decimals(item) for item in obj]
        return obj
    
    try:
        logger.info(f"Getting prompt improvement job status: {job_id}")
        
        # Get job from DynamoDB
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get('ANALYSIS_JOBS_TABLE', 'evaluation-analysis-jobs')
        table = dynamodb.Table(table_name)
        
        response = table.get_item(Key={'jobId': job_id})
        
        if 'Item' not in response:
            return {"error": "Job not found"}, 404
        
        job = response['Item']
        
        # Verify this is a prompt improvement job
        if job.get('jobType') != 'PROMPT_IMPROVEMENT':
            return {"error": "Invalid job type"}, 400
        
        logger.info(f"Job status: {job.get('status')}")
        
        # Return job status and results (convert Decimals to floats)
        result = {
            "jobId": job_id,
            "status": job.get('status'),
            "createdAt": job.get('createdAt'),
            "updatedAt": job.get('updatedAt')
        }
        
        # Include results if complete
        if job.get('status') == 'COMPLETED' and 'result' in job:
            result['result'] = convert_decimals(job['result'])
            logger.info(f"Returning completed prompt improvement job")
        
        # Include error if failed
        if job.get('status') == 'FAILED' and 'error' in job:
            result['error'] = job['error']
        
        return result
        
    except Exception as e:
        logger.error(f"Error getting prompt improvement status: {str(e)}", exc_info=True)
        return {"error": "Failed to get prompt improvement status"}, 500


@app.get("/evaluations/metrics")
@tracer.capture_method
def get_evaluation_metrics() -> Dict[str, Any]:
    """
    Get aggregated evaluation metrics from AgentCore
    
    Query parameters:
        config_id: Online evaluation configuration ID (required)
        start_date: ISO8601 datetime (default: 7 days ago)
        end_date: ISO8601 datetime (default: now)
        
    Returns:
        Aggregated evaluation metrics including scores and distributions
    """
    try:
        logger.info("get_evaluation_metrics")
        # Parse query parameters
        params = app.current_event.query_string_parameters or {}
        
        config_id = params.get("config_id")
        if not config_id:
            return {"error": "config_id parameter is required"}, 400
        
        # Time range (default to last 7 days)
        end_time = _parse_datetime(params.get("end_date"), datetime.utcnow())
        start_time = _parse_datetime(
            params.get("start_date"),
            end_time - timedelta(days=7)
        )
        
        logger.info(f"Getting evaluation metrics for config {config_id} from {start_time} to {end_time}")
        
        # Get metrics from AgentCore via CloudWatch
        metrics = agentcore_eval.get_evaluation_metrics(config_id, start_time, end_time)
        
        return metrics
        
    except Exception as e:
        logger.error(f"Error getting evaluation metrics: {str(e)}", exc_info=True)
        return {"error": "Failed to get evaluation metrics"}, 500


@app.post("/evaluations/setup")
@tracer.capture_method
def setup_evaluators() -> Dict[str, Any]:
    """
    Set up default AgentCore online evaluation for the runtime

    Request body:
        configName: (Optional) Name for the evaluation configuration
        samplingRate: (Optional) Sampling rate percentage (0.01-100.0, default: 10.0)
        enableOnCreate: (Optional) Enable immediately (default: true)

    Returns:
        Dictionary with configuration details
    """
    try:
        # Parse request body
        body = app.current_event.json_body or {}

        config_name = body.get("configName", "default_evaluation")
        sampling_rate = body.get("samplingRate", 10.0)
        enable_on_create = body.get("enableOnCreate", True)

        # Validate sampling rate
        if not isinstance(sampling_rate, (int, float)) or not 0.01 <= sampling_rate <= 100.0:
            return {"error": "samplingRate must be a number between 0.01 and 100.0"}, 400

        logger.info(f"Setting up default AgentCore evaluation: {config_name}")

        # Create default online evaluation
        result = agentcore_eval.setup_default_evaluation(
            config_name=config_name,
            sampling_rate=sampling_rate,
            enable_on_create=enable_on_create
        )

        # Check if configuration already existed
        if result.get('alreadyExists'):
            return {
                "message": "Evaluation configuration already exists and is active",
                "alreadyExists": True,
                **result
            }

        return {
            "message": "Successfully set up AgentCore online evaluation",
            **result
        }

    except Exception as e:
        logger.error(f"Error setting up evaluators: {str(e)}", exc_info=True)
        return {"error": "Failed to set up evaluators"}, 500



@app.put("/evaluations/configs/<config_id>")
@tracer.capture_method
def update_evaluation_config(config_id: str) -> Dict[str, Any]:
    """
    Update an online evaluation configuration
    
    Path parameters:
        config_id: Evaluation configuration ID
        
    Request body:
        executionStatus: (Optional) ENABLED or DISABLED
        samplingRate: (Optional) New sampling rate (0.01-100.0)
        evaluatorIds: (Optional) New list of evaluator IDs
        
    Returns:
        Update confirmation
    """
    try:
        logger.info("update_evaluation_config")
        # Parse request body
        body = app.current_event.json_body or {}
        
        execution_status = body.get("executionStatus")
        sampling_rate = body.get("samplingRate")
        evaluator_ids = body.get("evaluatorIds")
        
        # Validate inputs
        if execution_status and execution_status not in ['ENABLED', 'DISABLED']:
            return {"error": "executionStatus must be ENABLED or DISABLED"}, 400
        
        if sampling_rate is not None:
            if not isinstance(sampling_rate, (int, float)) or not 0.01 <= sampling_rate <= 100.0:
                return {"error": "samplingRate must be between 0.01 and 100.0"}, 400
        
        if evaluator_ids is not None:
            if not isinstance(evaluator_ids, list) or len(evaluator_ids) > 10:
                return {"error": "evaluatorIds must be a list with max 10 items"}, 400
        
        logger.info(f"Updating evaluation config: {config_id}")
        
        # Update configuration
        result = agentcore_eval.update_online_evaluation(
            config_id=config_id,
            execution_status=execution_status,
            sampling_rate=sampling_rate,
            evaluator_ids=evaluator_ids
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error updating evaluation config: {str(e)}", exc_info=True)
        return {"error": "Failed to update evaluation configuration"}, 500


@app.delete("/evaluations/configs/<config_id>")
@tracer.capture_method
def delete_evaluation_config(config_id: str) -> Dict[str, Any]:
    """
    Delete an online evaluation configuration
    
    Path parameters:
        config_id: Evaluation configuration ID
        
    Returns:
        Deletion confirmation
    """
    try:
        logger.info(f"Deleting evaluation config: {config_id}")
        
        result = agentcore_eval.delete_online_evaluation(config_id)
        
        return result
        
    except Exception as e:
        logger.error(f"Error deleting evaluation config: {str(e)}", exc_info=True)
        return {"error": "Failed to delete evaluation configuration"}, 500


@app.get("/evaluations/configs/<config_id>")
@tracer.capture_method
def get_evaluation_config(config_id: str) -> Dict[str, Any]:
    """
    Get details of an online evaluation configuration
    
    Path parameters:
        config_id: Evaluation configuration ID
        
    Returns:
        Configuration details
    """
    try:
        logger.info(f"Getting evaluation config: {config_id}")
        
        result = agentcore_eval.get_online_evaluation(config_id)
        
        return result
        
    except Exception as e:
        logger.error(f"Error getting evaluation config: {str(e)}", exc_info=True)
        return {"error": "Failed to get evaluation configuration"}, 500


@app.get("/evaluations/evaluators")
@tracer.capture_method
def list_evaluators() -> Dict[str, Any]:
    """
    List all available evaluators (built-in and custom)
    
    Returns:
        List of evaluator summaries
    """
    try:
        logger.info("Listing AgentCore evaluators")
        
        # Get built-in evaluators
        builtin_evaluators = agentcore_eval.list_builtin_evaluators()
        
        # Get custom evaluators
        custom_evaluators = agentcore_eval.list_custom_evaluators()
        
        return {
            "builtinEvaluators": builtin_evaluators,
            "customEvaluators": custom_evaluators,
            "totalBuiltin": len(builtin_evaluators),
            "totalCustom": len(custom_evaluators)
        }
        
    except Exception as e:
        logger.error(f"Error listing evaluators: {str(e)}", exc_info=True)
        return {"error": "Failed to list evaluators"}, 500


@app.get("/evaluations/configs")
@tracer.capture_method
def list_evaluation_configs() -> Dict[str, Any]:
    """
    List all online evaluation configurations
    
    Returns:
        List of online evaluation configurations
    """
    try:
        configs = agentcore_eval.list_online_evaluations()
        
        logger.info(f"Total configs returned: {len(configs)}")
        print(f"Configs retrieved: {configs}")
        
        response = {
            "configurations": configs,
            "count": len(configs)
        }
        
        return response
        
    except Exception as e:
        logger.error(f"Error listing evaluation configs: {str(e)}", exc_info=True)
        return {"error": "Failed to list evaluation configurations"}, 500


@app.post("/evaluations/evaluate")
@tracer.capture_method
def evaluate_on_demand() -> Dict[str, Any]:
    """
    Run on-demand evaluation on a specific session using AgentCore Starter toolkit
    
    Request body:
        sessionId: Session ID to evaluate (required)
        evaluatorId: Evaluator ID or list of evaluator IDs (required)
        
    Returns:
        Evaluation results
    """
    try:
        logger.info("evaluate_on_demand")
        # Parse request body
        body = app.current_event.json_body
        
        if not body:
            return {"error": "Request body is required"}, 400
        
        # Extract required parameters
        session_id = body.get("sessionId")
        evaluator_id = body.get("evaluatorId")
        
        if not session_id or not evaluator_id:
            return {"error": "sessionId and evaluatorId are required"}, 400
        
        # Get agent ID from runtime ARN
        runtime_arn = os.environ.get('RUNTIME_ARN', '')
        if not runtime_arn:
            return {"error": "RUNTIME_ARN not configured"}, 500
        
        # Extract agent ID from ARN
        # ARN format: arn:aws:bedrock-agentcore:region:account:runtime/agent_name-ID
        agent_id = runtime_arn.split('/')[-1] if runtime_arn else ''
        
        if not agent_id:
            return {"error": "Could not extract agent ID from RUNTIME_ARN"}, 500
        
        logger.info(f"Running on-demand evaluation for session {session_id} with evaluator {evaluator_id}")
        logger.info(f"Agent ID: {agent_id}")
        
        # Use AgentCore Starter toolkit to run evaluation
        # This automatically retrieves traces from AgentCore Observability
        evaluators = [evaluator_id] if isinstance(evaluator_id, str) else evaluator_id
        
        eval_results = agentcore_eval.eval_client.run(
            agent_id=agent_id,
            session_id=session_id,
            evaluators=evaluators
        )
        
        # Format results
        formatted_results = []
        for result in eval_results.results:
            formatted_results.append({
                "evaluatorId": result.evaluator_id,
                "evaluatorName": result.evaluator_name,
                "value": result.value,
                "label": result.label,
                "explanation": result.explanation,
                "tokenUsage": result.token_usage if hasattr(result, 'token_usage') else None,
                "context": result.context if hasattr(result, 'context') else None
            })
        
        logger.info(f"Evaluation complete: {len(formatted_results)} results")
        
        # Return evaluation results
        return {
            "sessionId": session_id,
            "evaluatorIds": evaluators,
            "evaluationResults": formatted_results,
            "resultCount": len(formatted_results)
        }
        
    except Exception as e:
        logger.error(f"Error running on-demand evaluation: {str(e)}", exc_info=True)
        return {"error": f"Failed to run evaluation: {str(e)}"}, 500


@app.post("/evaluations/evaluate-batch")
@tracer.capture_method
def evaluate_batch() -> Dict[str, Any]:
    """
    Run on-demand evaluation on multiple sessions with multiple evaluators
    
    Request body:
        sessionIds: List of session IDs to evaluate (required)
        evaluatorIds: List of evaluator IDs (required)
        
    Returns:
        Batch evaluation results
    """
    try:
        logger.info("evaluate_batch")
        # Parse request body
        body = app.current_event.json_body
        
        if not body:
            return {"error": "Request body is required"}, 400
        
        # Extract required parameters
        session_ids = body.get("sessionIds", [])
        evaluator_ids = body.get("evaluatorIds", [])
        
        if not session_ids or not evaluator_ids:
            return {"error": "sessionIds and evaluatorIds are required"}, 400
        
        if not isinstance(session_ids, list) or not isinstance(evaluator_ids, list):
            return {"error": "sessionIds and evaluatorIds must be arrays"}, 400
        
        logger.info(f"Running batch evaluation for {len(session_ids)} sessions with {len(evaluator_ids)} evaluators")
        
        # Run evaluations for each session and evaluator combination
        results = []
        errors = []
        
        for session_id in session_ids:
            for evaluator_id in evaluator_ids:
                try:
                    # Get session spans
                    session = cw_client.get_session_detail(session_id)
                    
                    if not session:
                        errors.append({
                            "sessionId": session_id,
                            "evaluatorId": evaluator_id,
                            "error": "Session not found"
                        })
                        continue
                    
                    # Convert to span logs
                    session_span_logs = []
                    for trace in session.traces:
                        for span in trace.spans:
                            span_log = {
                                "traceId": span.trace_id,
                                "spanId": span.span_id,
                                "name": span.name,
                                "startTimeUnixNano": int(span.start_time.timestamp() * 1_000_000_000),
                                "endTimeUnixNano": int(span.end_time.timestamp() * 1_000_000_000),
                                "attributes": span.attributes or {},
                                "status": {"code": span.status},
                                "scope": {"name": "bedrock-agentcore"}
                            }
                            span_log["attributes"]["session.id"] = session_id
                            session_span_logs.append(span_log)
                    
                    if not session_span_logs:
                        errors.append({
                            "sessionId": session_id,
                            "evaluatorId": evaluator_id,
                            "error": "No spans found"
                        })
                        continue
                    
                    # Call Evaluate API
                    bedrock_agentcore = boto3.client('bedrock-agentcore')
                    response = bedrock_agentcore.evaluate(
                        evaluatorId=evaluator_id,
                        evaluationInput={"sessionSpans": session_span_logs}
                    )
                    
                    results.append({
                        "sessionId": session_id,
                        "evaluatorId": evaluator_id,
                        "evaluationResults": response.get("evaluationResults", [])
                    })
                    
                except Exception as e:
                    logger.error(f"Error evaluating session {session_id} with {evaluator_id}: {e}")
                    errors.append({
                        "sessionId": session_id,
                        "evaluatorId": evaluator_id,
                        "error": str(e)
                    })
        
        return {
            "results": results,
            "errors": errors,
            "totalEvaluations": len(results),
            "totalErrors": len(errors)
        }
        
    except Exception as e:
        logger.error(f"Error running batch evaluation: {str(e)}", exc_info=True)
        return {"error": f"Failed to run batch evaluation: {str(e)}"}, 500


@app.post("/evaluations/evaluators/custom")
@tracer.capture_method
def create_custom_evaluator() -> Dict[str, Any]:
    """
    Create a custom evaluator
    
    Request body:
        name: Evaluator name (required)
        description: Evaluator description (required)
        modelId: Bedrock model ID (required)
        instructions: Evaluation instructions with placeholders (required)
        ratingScale: Rating scale configuration (required)
        evaluationLevel: TOOL_CALL, TRACE, or SESSION (default: TRACE)
        maxTokens: Max tokens for inference (default: 500)
        temperature: Temperature for inference (default: 1.0)
        
    Returns:
        Created evaluator ARN
    """
    try:
        logger.info("create_custom_evaluator")
        # Parse request body
        body = app.current_event.json_body
        
        if not body:
            return {"error": "Request body is required"}, 400
        
        # Extract and validate required parameters
        name = body.get("name")
        description = body.get("description")
        model_id = body.get("modelId")
        instructions = body.get("instructions")
        rating_scale = body.get("ratingScale")
        
        if not all([name, description, model_id, instructions, rating_scale]):
            return {
                "error": "Missing required fields: name, description, modelId, instructions, ratingScale"
            }, 400
        
        # Optional parameters
        evaluation_level = body.get("evaluationLevel", "TRACE")
        max_tokens = body.get("maxTokens", 500)
        temperature = body.get("temperature", 1.0)
        
        logger.info(f"Creating custom evaluator: {name}")
        
        # Create evaluator
        evaluator_arn = agentcore_eval.create_custom_evaluator(
            name=name,
            description=description,
            model_id=model_id,
            instructions=instructions,
            rating_scale=rating_scale,
            evaluation_level=evaluation_level,
            max_tokens=max_tokens,
            temperature=temperature
        )
        
        return {
            "evaluatorArn": evaluator_arn,
            "name": name,
            "message": "Successfully created custom evaluator"
        }
        
    except Exception as e:
        logger.error(f"Error creating custom evaluator: {str(e)}", exc_info=True)
        return {"error": "Failed to create custom evaluator"}, 500


@logger.inject_lambda_context(correlation_id_path=API_GATEWAY_REST)
@tracer.capture_lambda_handler
def handler(event: dict, context: LambdaContext) -> dict:
    """
    Lambda handler for evaluation API
    
    Args:
        event: API Gateway event or async invocation event
        context: Lambda context
        
    Returns:
        API Gateway response or None for async processing
    """
    logger.info(event)
    
    # Check if this is an async invocation for background processing
    if isinstance(event, dict) and event.get('action') == 'process_analysis':
        job_id = event.get('jobId')
        score_threshold = event.get('scoreThreshold', 0.5)
        limit = event.get('limit', 100)
        
        logger.info(f"Processing async analysis job: {job_id}")
        process_analysis_job(job_id, score_threshold, limit)
        return {}  # No response needed for async invocation
    
    # Check if this is an async invocation for prompt improvement
    if isinstance(event, dict) and event.get('action') == 'process_prompt_improvement':
        job_id = event.get('jobId')
        current_prompt = event.get('currentPrompt')
        analysis_id = event.get('analysisId')
        score_threshold = event.get('scoreThreshold', 0.5)
        limit = event.get('limit', 100)
        
        logger.info(f"Processing async prompt improvement job: {job_id}")
        process_prompt_improvement_job(job_id, current_prompt, analysis_id, score_threshold, limit)
        return {}  # No response needed for async invocation
    
    # Regular API Gateway request
    return app.resolve(event, context)
