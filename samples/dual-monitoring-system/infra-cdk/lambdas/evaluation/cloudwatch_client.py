"""
CloudWatch Client for AgentCore Evaluation Dashboard

Queries CloudWatch Logs for agent runtime sessions and traces.
Parses OpenTelemetry format logs from Bedrock AgentCore.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import boto3
from models import Session, SessionStatus, Span, Trace

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CloudWatchClient:
    """Client for querying CloudWatch Logs for agent runtime data"""

    # CloudWatch log group paths for Bedrock AgentCore Observability
    SPANS_LOG_GROUP = "aws/spans"  # Log group with 'default' stream (no leading slash)

    def __init__(self):
        """Initialize CloudWatch client"""
        self.client = boto3.client("logs")

        # Get runtime ARN from environment
        runtime_arn = os.environ.get("RUNTIME_ARN", "")

        # Extract runtime name from ARN for log group paths
        # ARN format: arn:aws:bedrock-agentcore:region:account:runtime/RuntimeName-ID
        if runtime_arn:
            runtime_name = runtime_arn.split("/")[-1]
            # Vended logs for runtime
            self.usage_logs = (
                f"/aws/vendedlogs/bedrock-agentcore/runtime/USAGE_LOGS/{runtime_name}"
            )
            self.app_logs = f"/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/{runtime_name}"
            # Standard runtime logs
            self.runtime_log_group = (
                f"/aws/bedrock-agentcore/runtimes/{runtime_name}-DEFAULT"
            )
        else:
            # Fallback
            self.usage_logs = "/aws/vendedlogs/bedrock-agentcore/runtime/USAGE_LOGS/"  # pragma: allowlist secret
            self.app_logs = "/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/"  # pragma: allowlist secret
            self.runtime_log_group = "/aws/bedrock-agentcore/runtimes/"

        # Retry configuration
        self.max_retries = 3
        self.retry_delay = 1  # seconds

    def _retry_with_backoff(self, func, *args, **kwargs):
        """Execute function with exponential backoff retry"""
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except self.client.exceptions.ThrottlingException:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2**attempt)
                    time.sleep(wait_time)
                else:
                    raise
            except Exception as e:
                if attempt < self.max_retries - 1 and "Rate exceeded" in str(e):
                    wait_time = self.retry_delay * (2**attempt)
                    time.sleep(wait_time)
                else:
                    raise

    def query_sessions(
        self,
        start_time: datetime,
        end_time: datetime,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        limit: int = 100,
    ) -> List[Session]:
        """
        Query runtime sessions from CloudWatch
        Gets sessions from app_logs with trace_ids, then gets spans by trace_id

        Args:
            start_time: Start of time range
            end_time: End of time range
            min_score: Minimum score filter (not used)
            max_score: Maximum score filter (not used)
            limit: Maximum number of sessions to return

        Returns:
            List of Session objects with trace and span counts
        """
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        # Step 1: Get sessions and trace_ids from app_logs
        logger.info(f"Querying app_logs: {self.app_logs}")
        sessions_dict = {}
        trace_to_session = {}

        try:
            next_token = None
            total_events = 0

            # Paginate through all app_logs
            while True:
                params = {
                    "logGroupName": self.app_logs,
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 10000,
                }

                if next_token:
                    params["nextToken"] = next_token

                response = self._retry_with_backoff(
                    self.client.filter_log_events, **params
                )

                events = response.get("events", [])
                total_events += len(events)

                for event in events:
                    try:
                        log_data = json.loads(event["message"])
                        session_id = log_data.get("session_id")
                        trace_id = log_data.get("trace_id")

                        if not session_id:
                            continue

                        # Parse timestamp (milliseconds)
                        event_ts = log_data.get("event_timestamp", event["timestamp"])
                        timestamp = datetime.fromtimestamp(event_ts / 1000)

                        # Check time range - log when filtering out
                        if timestamp < start_time or timestamp > end_time:
                            logger.debug(
                                f"Filtering out event: timestamp={timestamp}, start={start_time}, end={end_time}"
                            )
                            continue

                        # Create or update session
                        if session_id not in sessions_dict:
                            sessions_dict[session_id] = {
                                "session_id": session_id,
                                "timestamp": timestamp,
                                "trace_ids": set(),
                                "metadata": {},
                            }

                        # Add trace_id
                        if trace_id:
                            sessions_dict[session_id]["trace_ids"].add(trace_id)
                            trace_to_session[trace_id] = session_id

                        # Update timestamp to earliest
                        if timestamp < sessions_dict[session_id]["timestamp"]:
                            sessions_dict[session_id]["timestamp"] = timestamp

                    except Exception:
                        continue

                # Check for more pages
                next_token = response.get("nextToken")
                if not next_token:
                    break

                logger.info(
                    f"Fetching next page (processed {total_events} events so far)"
                )

            logger.info(
                f"Found {len(sessions_dict)} sessions, {len(trace_to_session)} traces from {total_events} events"
            )

        except Exception as e:
            logger.error(f"App logs query failed: {e}")
            return []

        # Step 2: Get spans by trace_id from spans log group
        logger.info(f"Querying spans: {self.SPANS_LOG_GROUP}")
        logger.info(f"Looking for {trace_to_session} traces")
        spans_by_trace = {}

        try:
            # Paginate through all spans
            next_token = None
            total_spans = 0

            while True:
                params = {
                    "logGroupName": self.SPANS_LOG_GROUP,
                    "logStreamNames": ["default"],
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 10000,
                }

                if next_token:
                    params["nextToken"] = next_token

                spans_response = self._retry_with_backoff(
                    self.client.filter_log_events, **params
                )

                events = spans_response.get("events", [])
                total_spans += len(events)

                # spans_response = self._retry_with_backoff(
                #     self.client.filter_log_events,
                #     logGroupName=self.SPANS_LOG_GROUP,
                #     logStreamNames=['default'],
                #     startTime=start_ms,
                #     endTime=end_ms,
                #     limit=10000
                # )

                logger.info(
                    f"Spans query returned {len(spans_response.get('events', []))} events"
                )

                for event in spans_response.get("events", []):
                    try:
                        log_data = json.loads(event["message"])
                        trace_id = log_data.get("traceId", "")
                        span_id = log_data.get("spanId", "")

                        if (
                            not trace_id
                            or not span_id
                            or trace_id not in trace_to_session
                        ):
                            continue

                        if trace_id not in spans_by_trace:
                            spans_by_trace[trace_id] = []

                        # Parse timestamps
                        start_ts = datetime.fromtimestamp(
                            log_data.get("startTimeUnixNano", 0) / 1_000_000_000
                        )
                        end_ts = datetime.fromtimestamp(
                            log_data.get("endTimeUnixNano", 0) / 1_000_000_000
                        )

                        span = Span(
                            span_id=span_id,
                            trace_id=trace_id,
                            parent_span_id=log_data.get("parentSpanId"),
                            name=log_data.get("name", "Unknown"),
                            start_time=start_ts,
                            end_time=end_ts,
                            attributes=log_data.get("attributes", {}),
                            status=log_data.get("status", "OK"),
                        )
                        spans_by_trace[trace_id].append(span)
                        # print(spans_by_trace)
                    except Exception as e:
                        logger.error(f"Error parsing span event: {e}", exc_info=True)
                        continue

                # Check for more pages
                next_token = spans_response.get("nextToken")
                if not next_token:
                    break

                logger.info(
                    f"Fetching next page of spans (processed {total_spans} events so far)"
                )

            logger.info(f"Found spans for {len(spans_by_trace)} traces")

        except Exception as e:
            logger.warning(f"Spans query failed: {e}")

        # Step 3: Build Session objects
        sessions = []
        for session_data in sessions_dict.values():
            traces = []

            for trace_id in session_data["trace_ids"]:
                spans = spans_by_trace.get(trace_id, [])
                if spans:
                    spans.sort(key=lambda s: s.start_time)
                    trace = Trace(
                        trace_id=trace_id,
                        spans=spans,
                        start_time=spans[0].start_time,
                        end_time=spans[-1].end_time,
                    )
                    traces.append(trace)

            traces.sort(key=lambda t: t.start_time)

            session = Session(
                session_id=session_data["session_id"],
                timestamp=session_data["timestamp"],
                traces=traces,
                evaluation=None,
                status=SessionStatus.COMPLETED,
                metadata={
                    "trace_count": len(traces),
                    "span_count": sum(len(t.spans) for t in traces),
                },
            )
            sessions.append(session)

        sessions.sort(key=lambda s: s.timestamp, reverse=True)
        logger.info(f"Returning {min(len(sessions), limit)} sessions")
        return sessions[:limit]

    def _query_sessions_from_log_group(
        self,
        log_group: str,
        start_time: datetime,
        end_time: datetime,
        limit: int,
        source: str,
    ) -> Dict[str, Session]:
        """Query sessions from a specific log group"""
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        logger.info(f"Querying {log_group} from {start_time} to {end_time}")

        # Get logs with proper limit
        response = self._retry_with_backoff(
            self.client.filter_log_events,
            logGroupName=log_group,
            startTime=start_ms,
            endTime=end_ms,
            limit=10000,
        )

        sessions_dict = {}
        events_by_session = {}
        events_processed = 0

        # First pass: group events by session
        for event in response.get("events", []):
            events_processed += 1
            try:
                log_data = json.loads(event["message"])

                # Extract session_id (app_logs have it at root level)
                session_id = log_data.get("session_id")

                # Also check other locations for compatibility
                if not session_id:
                    if "sessionId" in log_data:
                        session_id = log_data["sessionId"]
                    elif "attributes" in log_data and isinstance(
                        log_data["attributes"], dict
                    ):
                        session_id = log_data["attributes"].get(
                            "sessionId"
                        ) or log_data["attributes"].get("session.id")

                if not session_id:
                    continue

                if session_id not in events_by_session:
                    events_by_session[session_id] = []

                events_by_session[session_id].append((event, log_data))

            except (json.JSONDecodeError, KeyError, ValueError):
                continue

        logger.info(
            f"Processed {events_processed} events, found {len(events_by_session)} unique sessions"
        )

        # Second pass: build Session objects with traces and spans
        for session_id, session_events in events_by_session.items():
            try:
                traces_dict = {}
                session_timestamp = None

                for event, log_data in session_events:
                    # Parse timestamp
                    if "event_timestamp" in log_data:
                        # App logs use event_timestamp (can be int or string)
                        event_ts = log_data["event_timestamp"]
                        if isinstance(event_ts, str):
                            timestamp = datetime.fromisoformat(
                                event_ts.replace("Z", "+00:00")
                            )
                        else:
                            # It's a Unix timestamp in seconds
                            timestamp = datetime.fromtimestamp(event_ts)
                    elif "timestamp" in log_data and isinstance(
                        log_data["timestamp"], str
                    ):
                        timestamp = datetime.fromisoformat(
                            log_data["timestamp"].replace("Z", "+00:00")
                        )
                    elif "timeUnixNano" in log_data:
                        timestamp_nano = log_data["timeUnixNano"]
                        timestamp = datetime.fromtimestamp(
                            timestamp_nano / 1_000_000_000
                        )
                    else:
                        timestamp = datetime.fromtimestamp(event["timestamp"] / 1000)

                    # Only include sessions within the time range
                    if timestamp < start_time or timestamp > end_time:
                        continue

                    if session_timestamp is None or timestamp < session_timestamp:
                        session_timestamp = timestamp

                    # Extract trace and span IDs
                    trace_id = log_data.get("trace_id", log_data.get("traceId", ""))
                    span_id = log_data.get("span_id", log_data.get("spanId", ""))

                    if not trace_id:
                        continue

                    # Create or get trace
                    if trace_id not in traces_dict:
                        traces_dict[trace_id] = {
                            "trace_id": trace_id,
                            "spans": [],
                            "start_time": timestamp,
                            "end_time": timestamp,
                        }

                    # Update trace times
                    if timestamp < traces_dict[trace_id]["start_time"]:
                        traces_dict[trace_id]["start_time"] = timestamp
                    if timestamp > traces_dict[trace_id]["end_time"]:
                        traces_dict[trace_id]["end_time"] = timestamp

                    # Create span if we have span ID
                    if span_id:
                        # Extract span name from operation or other fields
                        name = log_data.get("operation", "Unknown")

                        # Try other name sources
                        if name == "Unknown":
                            scope_name = log_data.get("scope", {}).get("name", "")
                            if scope_name:
                                name = scope_name
                            elif "service_name" in log_data:
                                name = log_data["service_name"]

                        span = Span(
                            span_id=span_id,
                            trace_id=trace_id,
                            parent_span_id=None,
                            name=name,
                            start_time=timestamp,
                            end_time=timestamp,
                            attributes=log_data.get("attributes", {}),
                            status="OK",
                        )
                        traces_dict[trace_id]["spans"].append(span)

                # Skip sessions outside time range
                if session_timestamp and (
                    session_timestamp < start_time or session_timestamp > end_time
                ):
                    continue

                # Build Trace objects
                traces = []
                for trace_data in traces_dict.values():
                    trace = Trace(
                        trace_id=trace_data["trace_id"],
                        spans=trace_data["spans"],
                        start_time=trace_data["start_time"],
                        end_time=trace_data["end_time"],
                    )
                    traces.append(trace)

                # Create session with trace and span counts
                session = Session(
                    session_id=session_id,
                    timestamp=session_timestamp or datetime.utcnow(),
                    traces=traces,
                    evaluation=None,
                    status=SessionStatus.COMPLETED,
                    metadata={
                        "trace_id": list(traces_dict.keys())[0] if traces_dict else "",
                        "log_group": log_group,
                        "source": source,
                        "trace_count": len(traces),
                        "span_count": sum(
                            len(t["spans"]) for t in traces_dict.values()
                        ),
                    },
                )

                sessions_dict[session_id] = session

            except Exception as e:
                logger.warning(f"Error building session {session_id}: {e}")
                continue

        return sessions_dict

    def get_session_detail(self, session_id: str) -> Optional[Session]:
        """
        Get detailed session data including traces and spans
        Queries all available log groups

        Args:
            session_id: Session ID to retrieve

        Returns:
            Session object with traces and spans, or None if not found
        """
        # Try all log sources
        log_sources = [
            ("usage_logs", self.usage_logs),
            ("app_logs", self.app_logs),
            ("runtime_logs", self.runtime_log_group),
            ("spans", self.SPANS_LOG_GROUP),
        ]

        session = None

        for source_name, log_group in log_sources:
            try:
                logger.info(f"Querying {source_name} for session {session_id}")
                source_session = self._get_session_from_log_group(
                    session_id, log_group, source_name
                )

                if source_session:
                    if not session:
                        session = source_session
                    else:
                        # Merge traces from multiple sources
                        session.traces.extend(source_session.traces)

            except self.client.exceptions.ResourceNotFoundException:
                logger.info(f"Log group not found: {log_group}")
            except Exception as e:
                logger.warning(f"Error querying {source_name}: {e}")

        return session

    def _get_session_from_log_group(
        self, session_id: str, log_group: str, source: str
    ) -> Optional[Session]:
        """Get session detail from a specific log group"""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=7)

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        # Get all logs and filter in code (filter pattern is too restrictive)
        response = self._retry_with_backoff(
            self.client.filter_log_events,
            logGroupName=log_group,
            startTime=start_ms,
            endTime=end_ms,
            limit=5000,  # Get more logs to find all session data
        )

        if not response.get("events"):
            return None

        # Filter events for this session
        session_events = []
        for event in response["events"]:
            try:
                log_data = json.loads(event["message"])

                # Check if this log belongs to our session
                found_session = False
                if log_data.get("sessionId") == session_id:
                    found_session = True
                elif "attributes" in log_data:
                    attrs = log_data["attributes"]
                    if (
                        attrs.get("sessionId") == session_id
                        or attrs.get("session.id") == session_id
                    ):
                        found_session = True

                if found_session:
                    session_events.append(event)

            except (json.JSONDecodeError, KeyError):
                continue

        if not session_events:
            return None

        return self._build_session_from_events(session_id, session_events, source)

    def _build_session_from_events(
        self, session_id: str, events: List[Dict], source: str
    ) -> Optional[Session]:
        """Build session object from log events"""
        traces_dict = {}
        session_timestamp = None

        for event in events:
            try:
                log_data = json.loads(event["message"])

                # Extract trace and span IDs
                trace_id = log_data.get("traceId", "")
                span_id = log_data.get("spanId", "")

                if not trace_id:
                    continue

                # Parse start/end timestamps from span data (nanoseconds)
                start_nano = log_data.get("startTimeUnixNano", 0)
                end_nano = log_data.get("endTimeUnixNano", 0)

                if start_nano and end_nano:
                    start_ts = datetime.fromtimestamp(start_nano / 1_000_000_000)
                    end_ts = datetime.fromtimestamp(end_nano / 1_000_000_000)
                else:
                    # Fallback to event timestamp
                    timestamp_nano = log_data.get(
                        "timeUnixNano", event["timestamp"] * 1000000
                    )
                    start_ts = datetime.fromtimestamp(timestamp_nano / 1_000_000_000)
                    end_ts = start_ts

                if session_timestamp is None or start_ts < session_timestamp:
                    session_timestamp = start_ts

                # Create or get trace
                if trace_id not in traces_dict:
                    traces_dict[trace_id] = {
                        "trace_id": trace_id,
                        "spans": [],
                        "start_time": start_ts,
                        "end_time": end_ts,
                    }

                # Update trace time bounds
                if start_ts < traces_dict[trace_id]["start_time"]:
                    traces_dict[trace_id]["start_time"] = start_ts
                if end_ts > traces_dict[trace_id]["end_time"]:
                    traces_dict[trace_id]["end_time"] = end_ts

                # Create span if we have span ID
                if span_id:
                    # Use the 'name' field first (standard OTel span name)
                    name = log_data.get("name", "")

                    # Fallback to scope name or body content
                    if not name:
                        scope_name = log_data.get("scope", {}).get("name", "")
                        if scope_name:
                            name = scope_name
                        else:
                            body = log_data.get("body", {})
                            if isinstance(body, dict):
                                content = body.get("content", [])
                                if content and isinstance(content, list):
                                    name = str(content[0].get("text", "Unknown"))[:100]
                            elif isinstance(body, str):
                                name = body[:100]
                            else:
                                name = "Unknown"

                    # Parse parent span ID
                    parent_span_id = log_data.get("parentSpanId")

                    # Parse status
                    status_data = log_data.get("status", {})
                    if isinstance(status_data, dict):
                        status = status_data.get("code", "OK")
                    else:
                        status = str(status_data) if status_data else "OK"

                    span = Span(
                        span_id=span_id,
                        trace_id=trace_id,
                        parent_span_id=parent_span_id,
                        name=name,
                        start_time=start_ts,
                        end_time=end_ts,
                        attributes=log_data.get("attributes", {}),
                        status=status,
                    )
                    traces_dict[trace_id]["spans"].append(span)

            except (json.JSONDecodeError, KeyError):
                continue

        if not traces_dict:
            return None

        # Build Trace objects
        traces = []
        for trace_data in traces_dict.values():
            # Sort spans by start time
            trace_data["spans"].sort(key=lambda s: s.start_time)

            trace = Trace(
                trace_id=trace_data["trace_id"],
                spans=trace_data["spans"],
                start_time=trace_data["start_time"],
                end_time=trace_data["end_time"],
            )
            traces.append(trace)

        # Sort traces by start time
        traces.sort(key=lambda t: t.start_time)

        # Create session
        session = Session(
            session_id=session_id,
            timestamp=session_timestamp or datetime.utcnow(),
            traces=traces,
            evaluation=None,
            status=SessionStatus.COMPLETED,
            metadata={
                "log_group": self.runtime_log_group
                if source == "runtime_logs"
                else self.SPANS_LOG_GROUP,
                "source": source,
            },
        )

        return session
