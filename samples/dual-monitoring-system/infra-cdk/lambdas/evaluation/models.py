# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data models for evaluation dashboard"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum


class SessionStatus(Enum):
    """Status of an agent session"""
    COMPLETED = "completed"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"


@dataclass
class Span:
    """OpenTelemetry span representation"""
    span_id: str
    trace_id: str
    parent_span_id: Optional[str]
    name: str
    start_time: datetime
    end_time: datetime
    attributes: Dict[str, Any] = field(default_factory=dict)
    status: str = "OK"
    
    @property
    def duration_ms(self) -> int:
        """Duration in milliseconds"""
        return int((self.end_time - self.start_time).total_seconds() * 1000)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API response"""
        return {
            "spanId": self.span_id,
            "traceId": self.trace_id,
            "parentSpanId": self.parent_span_id,
            "name": self.name,
            "startTime": self.start_time.isoformat(),
            "endTime": self.end_time.isoformat(),
            "durationMs": self.duration_ms,
            "attributes": self.attributes,
            "status": self.status
        }


@dataclass
class Trace:
    """Collection of related spans"""
    trace_id: str
    spans: List[Span]
    start_time: datetime
    end_time: datetime
    
    @property
    def root_span(self) -> Optional[Span]:
        """Get the root span (no parent)"""
        for span in self.spans:
            if span.parent_span_id is None:
                return span
        return None
    
    @property
    def duration_ms(self) -> int:
        """Total trace duration in milliseconds"""
        return int((self.end_time - self.start_time).total_seconds() * 1000)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API response"""
        return {
            "traceId": self.trace_id,
            "spans": [span.to_dict() for span in self.spans],
            "startTime": self.start_time.isoformat(),
            "endTime": self.end_time.isoformat(),
            "durationMs": self.duration_ms
        }


@dataclass
class EvaluationResult:
    """Evaluation score and metadata"""
    evaluation_id: str
    session_id: str
    score: float
    criteria: Dict[str, float]
    feedback: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API response"""
        return {
            "evaluationId": self.evaluation_id,
            "sessionId": self.session_id,
            "score": self.score,
            "criteria": self.criteria,
            "feedback": self.feedback,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class Session:
    """Complete agent session with traces and evaluation"""
    session_id: str
    timestamp: datetime
    traces: List[Trace]
    evaluation: Optional[EvaluationResult]
    status: SessionStatus
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def score(self) -> Optional[float]:
        """Evaluation score if available"""
        return self.evaluation.score if self.evaluation else None
    
    @property
    def trace_count(self) -> int:
        """Number of traces in session"""
        return len(self.traces)
    
    @property
    def span_count(self) -> int:
        """Total number of spans across all traces"""
        return sum(len(trace.spans) for trace in self.traces)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API response"""
        return {
            "sessionId": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "score": self.score,
            "traceCount": self.trace_count,
            "spanCount": self.span_count,
            "status": self.status.value,
            "metadata": self.metadata,
            "traces": [trace.to_dict() for trace in self.traces],
            "evaluation": self.evaluation.to_dict() if self.evaluation else None
        }


@dataclass
class Pattern:
    """Identified pattern in evaluation data"""
    pattern: str
    frequency: int
    affected_sessions: List[str]
    evidence: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API response"""
        return {
            "pattern": self.pattern,
            "frequency": self.frequency,
            "affectedSessions": self.affected_sessions,
            "evidence": self.evidence
        }


@dataclass
class AnalysisResult:
    """AI analysis output"""
    analysis_id: str
    patterns: List[Pattern]
    summary: str
    recommendations: List[str]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API response"""
        return {
            "analysisId": self.analysis_id,
            "patterns": [p.to_dict() for p in self.patterns],
            "summary": self.summary,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class PromptChange:
    """Individual change in prompt improvement"""
    section: str
    reasoning: str
    impact: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API response"""
        return {
            "section": self.section,
            "reasoning": self.reasoning,
            "impact": self.impact
        }


@dataclass
class PromptImprovement:
    """Prompt improvement suggestion"""
    improvement_id: str
    original_prompt: str
    improved_prompt: str
    changes: List[PromptChange]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API response"""
        return {
            "improvementId": self.improvement_id,
            "originalPrompt": self.original_prompt,
            "improvedPrompt": self.improved_prompt,
            "changes": [c.to_dict() for c in self.changes],
            "timestamp": self.timestamp.isoformat()
        }
