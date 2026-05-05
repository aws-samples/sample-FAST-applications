"""
Improved CloudWatch Client for AgentCore Evaluation Dashboard

Uses CloudWatch Logs Insights for efficient querying of sessions, traces, and spans.
This approach is more efficient than filter_log_events for complex queries.
"""

import boto3
import json
import os
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from models import Span, Trace, Session, SessionStatus

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ImprovedCloudWatchClient:
    """
    Improved client using CloudWatch Logs Insights for efficient querying
    
    Benefits over filter_log_events:
    - More efficient for large datasets
    - Built-in aggregation and filtering
    - Better performance with complex queries
    - Supports joins across log groups
    """
    
    # CloudWatch log group paths
    SPANS_LOG_GROUP = "aws/spans"
    
    def __init__(self):
        """Initialize CloudWatch client"""
        self.client = boto3.client('logs')
        
        # Get runtime ARN from environment
        runtime_arn = os.environ.get('RUNTIME_ARN', '')
        
        if runtime_arn:
            runtime_name = runtime_arn.split('/')[-1]
            self.usage_logs = f"/aws/vendedlogs/bedrock-agentcore/runtime/USAGE_LOGS/{runtime_name}"
            self.app_logs = f"/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/{runtime_name}"
            self.runtime_logs = f"/aws/vendedlogs/bedrock-agentcore/runtime/RUNTIME_LOGS/{runtime_name}"
            self.otel_logs = f"/aws/vendedlogs/bedrock-agentcore/runtime/OTEL_LOGS/{runtime_name}"
        else:
            self.usage_logs = "/aws/vendedlogs/bedrock-agentcore/runtime/USAGE_LOGS/"
            self.app_logs = "/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/"
            self.runtime_logs = "/aws/vendedlogs/bedrock-agentcore/runtime/RUNTIME_LOGS/"
            self.otel_logs = "/aws/vendedlogs/bedrock-agentcore/runtime/OTEL_LOGS/"
    
    def _run_insights_query(
        self,
        query_string: str,
        log_groups: List[str],
        start_time: datetime,
        end_time: datetime,
        max_wait_seconds: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Run a CloudWatch Logs Insights query and wait for results
        
        Args:
            query_string: Logs Insights query
            log_groups: List of log group names to query
            start_time: Start of time range
            end_time: End of time range
            max_wait_seconds: Maximum time to wait for query completion
            
        Returns:
            List of query result records (empty list if log groups don't exist)
        """
        # Validate and adjust time range
        now = datetime.utcnow()
        
        # Ensure end_time is not in the future
        if end_time > now:
            end_time = now
        
        # Ensure start_time is not too far in the past (respect log retention)
        # Default retention is 7 days, but we'll be conservative
        max_lookback = now - timedelta(days=7)
        if start_time < max_lookback:
            logger.info(f"Adjusting start_time from {start_time} to {max_lookback} (log retention limit)")
            start_time = max_lookback
        
        # Ensure start_time is before end_time
        if start_time >= end_time:
            logger.warning(f"Invalid time range: start_time ({start_time}) >= end_time ({end_time})")
            return []
        
        start_ms = int(start_time.timestamp())
        end_ms = int(end_time.timestamp())
        
        # Filter out non-existent log groups
        existing_log_groups = []
        for log_group in log_groups:
            try:
                response = self.client.describe_log_groups(logGroupNamePrefix=log_group, limit=1)
                log_group_info = response.get('logGroups', [])
                
                if log_group_info:
                    # Check log group creation time
                    creation_time_ms = log_group_info[0].get('creationTime', 0)
                    creation_time = datetime.fromtimestamp(creation_time_ms / 1000)
                    
                    # Adjust start_time if it's before log group creation
                    if start_time < creation_time:
                        logger.info(f"Adjusting start_time to log group creation time: {creation_time}")
                        start_time = creation_time
                        start_ms = int(start_time.timestamp())
                    
                    existing_log_groups.append(log_group)
                else:
                    logger.info(f"Log group not found (will be created on first agent invocation): {log_group}")
                    
            except self.client.exceptions.ResourceNotFoundException:
                logger.info(f"Log group not found (will be created on first agent invocation): {log_group}")
                continue
            except Exception as e:
                logger.warning(f"Error checking log group {log_group}: {e}")
                continue
        
        if not existing_log_groups:
            logger.info("No existing log groups found - returning empty results")
            return []
        
        # Re-validate time range after adjustments
        if start_ms >= end_ms:
            logger.warning(f"Time range invalid after adjustments - returning empty results")
            return []
        
        try:
            # Start the query
            response = self.client.start_query(
                logGroupNames=existing_log_groups,
                startTime=start_ms,
                endTime=end_ms,
                queryString=query_string,
                limit=10000
            )
            
            query_id = response['queryId']
            logger.info(f"Started Logs Insights query: {query_id}")
            
            # Poll for results
            start_poll = time.time()
            while time.time() - start_poll < max_wait_seconds:
                result = self.client.get_query_results(queryId=query_id)
                status = result['status']
                
                if status == 'Complete':
                    logger.info(f"Query completed: {len(result.get('results', []))} results")
                    return result.get('results', [])
                elif status == 'Failed':
                    logger.error(f"Query failed")
                    return []
                elif status in ['Cancelled', 'Timeout']:
                    logger.warning(f"Query {status.lower()}")
                    return []
                
                # Still running, wait a bit
                time.sleep(0.5)
            
            # Timeout
            logger.warning(f"Query timed out after {max_wait_seconds}s")
            self.client.stop_query(queryId=query_id)
            return []
            
        except self.client.exceptions.ResourceNotFoundException as e:
            logger.info(f"Log group not found: {e}. This is normal if no agent invocations have occurred yet.")
            return []
        except self.client.exceptions.MalformedQueryException as e:
            logger.warning(f"Malformed query (likely time range issue): {e}")
            return []
        except Exception as e:
            logger.warning(f"Query failed: {e}")
            return []
    
    def query_sessions(
        self,
        start_time: datetime,
        end_time: datetime,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        limit: int = 100
    ) -> List[Session]:
        """
        Query sessions using CloudWatch Logs Insights
        
        This is more efficient than filter_log_events for aggregating session data.
        
        Args:
            start_time: Start of time range
            end_time: End of time range
            min_score: Minimum score filter
            max_score: Maximum score filter
            limit: Maximum number of sessions
            
        Returns:
            List of Session objects
        """
        # Query to get unique sessions with their trace counts
        query = """
        fields session_id, trace_id, event_timestamp
        | filter ispresent(session_id)
        | stats 
            min(event_timestamp) as first_seen,
            max(event_timestamp) as last_seen,
            count_distinct(trace_id) as trace_count
          by session_id
        | sort first_seen desc
        | limit {limit}
        """.format(limit=limit)
        
        logger.info(f"Querying sessions from {start_time} to {end_time}")
        
        # Query application logs for sessions
        results = self._run_insights_query(
            query_string=query,
            log_groups=[self.app_logs],
            start_time=start_time,
            end_time=end_time
        )
        
        if not results:
            logger.info("No sessions found in application logs")
            return []
        
        # Build Session objects
        sessions = []
        for record in results:
            try:
                # Parse fields from query results
                fields = {item['field']: item['value'] for item in record}
                
                session_id = fields.get('session_id')
                if not session_id:
                    continue
                
                # Parse timestamp (milliseconds)
                first_seen_ms = float(fields.get('first_seen', 0))
                timestamp = datetime.fromtimestamp(first_seen_ms / 1000)
                
                trace_count = int(fields.get('trace_count', 0))
                
                # Create session object
                session = Session(
                    session_id=session_id,
                    timestamp=timestamp,
                    traces=[],  # Traces loaded on-demand in get_session_detail
                    evaluation=None,
                    status=SessionStatus.COMPLETED,
                    metadata={
                        'trace_count': trace_count,
                        'span_count': 0,  # Will be calculated when traces are loaded
                        'source': 'logs_insights'
                    }
                )
                sessions.append(session)
                
            except Exception as e:
                logger.warning(f"Error parsing session record: {e}")
                continue
        
        logger.info(f"Found {len(sessions)} sessions")
        return sessions[:limit]
    
    def get_session_detail(self, session_id: str) -> Optional[Session]:
        """
        Get detailed session data including traces and spans using Logs Insights
        
        Args:
            session_id: Session ID to retrieve
            
        Returns:
            Session object with traces and spans
        """
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=7)
        
        # Step 1: Get all trace IDs for this session
        trace_query = """
        fields session_id, trace_id, event_timestamp
        | filter session_id = "{session_id}"
        | stats 
            min(event_timestamp) as start_time,
            max(event_timestamp) as end_time
          by trace_id
        | sort start_time asc
        """.format(session_id=session_id)
        
        trace_results = self._run_insights_query(
            query_string=trace_query,
            log_groups=[self.app_logs],
            start_time=start_time,
            end_time=end_time
        )
        
        if not trace_results:
            logger.info(f"No traces found for session {session_id}")
            return None
        
        # Step 2: Get spans for each trace from spans log group
        traces = []
        session_timestamp = None
        
        for trace_record in trace_results:
            try:
                fields = {item['field']: item['value'] for item in trace_record}
                trace_id = fields.get('trace_id')
                
                if not trace_id:
                    continue
                
                # Parse trace timestamps
                start_ms = float(fields.get('start_time', 0))
                end_ms = float(fields.get('end_time', 0))
                trace_start = datetime.fromtimestamp(start_ms / 1000)
                trace_end = datetime.fromtimestamp(end_ms / 1000)
                
                if session_timestamp is None:
                    session_timestamp = trace_start
                
                # Query spans for this trace
                spans = self._get_spans_for_trace(trace_id, start_time, end_time)
                
                if spans:
                    trace = Trace(
                        trace_id=trace_id,
                        spans=spans,
                        start_time=trace_start,
                        end_time=trace_end
                    )
                    traces.append(trace)
                    
            except Exception as e:
                logger.warning(f"Error processing trace: {e}")
                continue
        
        if not traces:
            return None
        
        # Build session
        session = Session(
            session_id=session_id,
            timestamp=session_timestamp or datetime.utcnow(),
            traces=traces,
            evaluation=None,
            status=SessionStatus.COMPLETED,
            metadata={
                'trace_count': len(traces),
                'span_count': sum(len(t.spans) for t in traces),
                'source': 'logs_insights'
            }
        )
        
        return session
    
    def _get_spans_for_trace(
        self,
        trace_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[Span]:
        """
        Get all spans for a specific trace using Logs Insights
        
        Args:
            trace_id: Trace ID to query
            start_time: Start of time range
            end_time: End of time range
            
        Returns:
            List of Span objects
        """
        # Query spans log group for this trace
        span_query = """
        fields traceId, spanId, parentSpanId, name, startTimeUnixNano, endTimeUnixNano, attributes, status
        | filter traceId = "{trace_id}"
        | sort startTimeUnixNano asc
        """.format(trace_id=trace_id)
        
        span_results = self._run_insights_query(
            query_string=span_query,
            log_groups=[self.SPANS_LOG_GROUP],
            start_time=start_time,
            end_time=end_time,
            max_wait_seconds=10
        )
        
        spans = []
        for span_record in span_results:
            try:
                fields = {item['field']: item['value'] for item in span_record}
                
                span_id = fields.get('spanId')
                if not span_id:
                    continue
                
                # Parse timestamps (nanoseconds)
                start_nano = int(fields.get('startTimeUnixNano', 0))
                end_nano = int(fields.get('endTimeUnixNano', 0))
                start_ts = datetime.fromtimestamp(start_nano / 1_000_000_000)
                end_ts = datetime.fromtimestamp(end_nano / 1_000_000_000)
                
                # Parse attributes (may be JSON string)
                attributes_str = fields.get('attributes', '{}')
                try:
                    attributes = json.loads(attributes_str) if isinstance(attributes_str, str) else attributes_str
                except json.JSONDecodeError:
                    attributes = {}
                
                span = Span(
                    span_id=span_id,
                    trace_id=trace_id,
                    parent_span_id=fields.get('parentSpanId'),
                    name=fields.get('name', 'Unknown'),
                    start_time=start_ts,
                    end_time=end_ts,
                    attributes=attributes,
                    status=fields.get('status', 'OK')
                )
                spans.append(span)
                
            except Exception as e:
                logger.warning(f"Error parsing span: {e}")
                continue
        
        return spans
    
    def get_session_statistics(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """
        Get aggregated session statistics using Logs Insights
        
        Args:
            start_time: Start of time range
            end_time: End of time range
            
        Returns:
            Dictionary with session statistics
        """
        query = """
        fields session_id, score
        | filter ispresent(session_id)
        | stats 
            count_distinct(session_id) as total_sessions,
            avg(score) as average_score,
            min(score) as min_score,
            max(score) as max_score
        """
        
        results = self._run_insights_query(
            query_string=query,
            log_groups=[self.app_logs],
            start_time=start_time,
            end_time=end_time
        )
        
        if not results or not results[0]:
            return {
                'total_sessions': 0,
                'average_score': 0.0,
                'min_score': 0.0,
                'max_score': 0.0
            }
        
        fields = {item['field']: item['value'] for item in results[0]}
        
        return {
            'total_sessions': int(fields.get('total_sessions', 0)),
            'average_score': float(fields.get('average_score', 0.0)),
            'min_score': float(fields.get('min_score', 0.0)),
            'max_score': float(fields.get('max_score', 0.0))
        }
    
    def query_sessions_by_score(
        self,
        start_time: datetime,
        end_time: datetime,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        limit: int = 100
    ) -> List[Session]:
        """
        Query sessions filtered by score range using Logs Insights
        
        Args:
            start_time: Start of time range
            end_time: End of time range
            min_score: Minimum score threshold
            max_score: Maximum score threshold
            limit: Maximum number of sessions
            
        Returns:
            List of Session objects
        """
        # Build score filter
        score_filter = ""
        if min_score is not None and max_score is not None:
            score_filter = f"| filter score >= {min_score} and score <= {max_score}"
        elif min_score is not None:
            score_filter = f"| filter score >= {min_score}"
        elif max_score is not None:
            score_filter = f"| filter score <= {max_score}"
        
        query = """
        fields session_id, trace_id, event_timestamp, score
        | filter ispresent(session_id)
        {score_filter}
        | stats 
            min(event_timestamp) as first_seen,
            max(event_timestamp) as last_seen,
            count_distinct(trace_id) as trace_count,
            avg(score) as avg_score
          by session_id
        | sort first_seen desc
        | limit {limit}
        """.format(score_filter=score_filter, limit=limit)
        
        results = self._run_insights_query(
            query_string=query,
            log_groups=[self.app_logs],
            start_time=start_time,
            end_time=end_time
        )
        
        sessions = []
        for record in results:
            try:
                fields = {item['field']: item['value'] for item in record}
                
                session_id = fields.get('session_id')
                if not session_id:
                    continue
                
                first_seen_ms = float(fields.get('first_seen', 0))
                timestamp = datetime.fromtimestamp(first_seen_ms / 1000)
                
                trace_count = int(fields.get('trace_count', 0))
                score = float(fields.get('avg_score', 0.0)) if fields.get('avg_score') else None
                
                session = Session(
                    session_id=session_id,
                    timestamp=timestamp,
                    traces=[],
                    evaluation=None,
                    status=SessionStatus.COMPLETED,
                    metadata={
                        'trace_count': trace_count,
                        'span_count': 0,
                        'source': 'logs_insights',
                        'score': score
                    }
                )
                sessions.append(session)
                
            except Exception as e:
                logger.warning(f"Error parsing session: {e}")
                continue
        
        return sessions
    
    def get_trace_ids_for_session(self, session_id: str, start_time: datetime, end_time: datetime) -> List[str]:
        """
        Get all trace IDs for a session using Logs Insights
        
        Args:
            session_id: Session ID
            start_time: Start of time range
            end_time: End of time range
            
        Returns:
            List of trace IDs
        """
        query = """
        fields trace_id
        | filter session_id = "{session_id}"
        | stats count() by trace_id
        """.format(session_id=session_id)
        
        results = self._run_insights_query(
            query_string=query,
            log_groups=[self.app_logs],
            start_time=start_time,
            end_time=end_time,
            max_wait_seconds=10
        )
        
        trace_ids = []
        for record in results:
            fields = {item['field']: item['value'] for item in record}
            trace_id = fields.get('trace_id')
            if trace_id:
                trace_ids.append(trace_id)
        
        return trace_ids
