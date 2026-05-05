# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Basic tests for AI Analysis Engine"""

import json
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from ai_engine import AIAnalysisEngine
from models import (
    Session,
    Trace,
    Span,
    EvaluationResult,
    SessionStatus,
    Pattern,
    AnalysisResult,
    PromptImprovement
)


def create_mock_bedrock_client():
    """Create a mock Bedrock client for testing"""
    mock_client = Mock()
    return mock_client


def create_test_session(session_id: str, score: float) -> Session:
    """Create a test session with sample data"""
    now = datetime.utcnow()
    
    # Create sample spans
    spans = [
        Span(
            span_id=f"span-{session_id}-1",
            trace_id=f"trace-{session_id}",
            parent_span_id=None,
            name="root_operation",
            start_time=now,
            end_time=now + timedelta(seconds=2),
            attributes={"operation": "query"},
            status="OK"
        ),
        Span(
            span_id=f"span-{session_id}-2",
            trace_id=f"trace-{session_id}",
            parent_span_id=f"span-{session_id}-1",
            name="child_operation",
            start_time=now + timedelta(seconds=0.5),
            end_time=now + timedelta(seconds=1.5),
            attributes={"operation": "process"},
            status="ERROR" if score < 0.5 else "OK"
        )
    ]
    
    # Create trace
    trace = Trace(
        trace_id=f"trace-{session_id}",
        spans=spans,
        start_time=now,
        end_time=now + timedelta(seconds=2)
    )
    
    # Create evaluation
    evaluation = EvaluationResult(
        evaluation_id=f"eval-{session_id}",
        session_id=session_id,
        score=score,
        criteria={"accuracy": score, "completeness": score},
        feedback="Test feedback",
        timestamp=now
    )
    
    # Create session
    session = Session(
        session_id=session_id,
        timestamp=now,
        traces=[trace],
        evaluation=evaluation,
        status=SessionStatus.COMPLETED if score >= 0.5 else SessionStatus.FAILED
    )
    
    return session


def test_ai_engine_initialization():
    """Test that AI engine initializes correctly"""
    mock_bedrock = create_mock_bedrock_client()
    engine = AIAnalysisEngine(mock_bedrock)
    
    assert engine.bedrock == mock_bedrock
    assert engine.model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"
    assert engine.pattern_agent_instructions is not None
    assert engine.prompt_agent_instructions is not None
    
    print("✓ AI engine initialization test passed")


def test_format_sessions_for_analysis():
    """Test session formatting for AI analysis"""
    mock_bedrock = create_mock_bedrock_client()
    engine = AIAnalysisEngine(mock_bedrock)
    
    # Create test sessions
    sessions = [
        create_test_session("session-1", 0.3),
        create_test_session("session-2", 0.4)
    ]
    
    # Format sessions
    formatted = engine._format_sessions_for_analysis(sessions)
    
    # Verify it's valid JSON
    data = json.loads(formatted)
    assert len(data) == 2
    assert data[0]["sessionId"] == "session-1"
    assert data[0]["score"] == 0.3
    assert "traces" in data[0]
    assert "evaluation" in data[0]
    
    print("✓ Session formatting test passed")


def test_parse_analysis_result():
    """Test parsing of AI analysis response"""
    mock_bedrock = create_mock_bedrock_client()
    engine = AIAnalysisEngine(mock_bedrock)
    
    # Mock AI response
    mock_response = """
```json
{
  "patterns": [
    {
      "pattern": "High error rate in child operations",
      "frequency": 5,
      "affectedSessions": ["session-1", "session-2"],
      "evidence": "Multiple spans with ERROR status"
    }
  ],
  "summary": "Analysis identified error handling issues",
  "recommendations": ["Improve error handling", "Add retry logic"]
}
```
"""
    
    sessions = [create_test_session("session-1", 0.3)]
    result = engine._parse_analysis_result(mock_response, sessions)
    
    assert isinstance(result, AnalysisResult)
    assert len(result.patterns) == 1
    assert result.patterns[0].pattern == "High error rate in child operations"
    assert result.patterns[0].frequency == 5
    assert len(result.patterns[0].affected_sessions) == 2
    assert result.summary == "Analysis identified error handling issues"
    assert len(result.recommendations) == 2
    
    print("✓ Analysis result parsing test passed")


def test_parse_prompt_improvement():
    """Test parsing of prompt improvement response"""
    mock_bedrock = create_mock_bedrock_client()
    engine = AIAnalysisEngine(mock_bedrock)
    
    original_prompt = "You are a helpful assistant."
    
    # Mock AI response
    mock_response = """
```json
{
  "improvedPrompt": "You are a helpful assistant with strong error handling.",
  "changes": [
    {
      "section": "Error Handling",
      "reasoning": "Add explicit error handling guidance",
      "impact": "Reduce error rate by 30%"
    }
  ]
}
```
"""
    
    result = engine._parse_prompt_improvement(mock_response, original_prompt)
    
    assert isinstance(result, PromptImprovement)
    assert result.original_prompt == original_prompt
    assert "error handling" in result.improved_prompt.lower()
    assert len(result.changes) == 1
    assert result.changes[0].section == "Error Handling"
    
    print("✓ Prompt improvement parsing test passed")


def test_analyze_patterns_with_mock():
    """Test pattern analysis with mocked Bedrock response"""
    mock_bedrock = create_mock_bedrock_client()
    
    # Mock Bedrock response
    mock_response_body = {
        "content": [
            {
                "text": json.dumps({
                    "patterns": [
                        {
                            "pattern": "Test pattern",
                            "frequency": 3,
                            "affectedSessions": ["s1", "s2", "s3"],
                            "evidence": "Test evidence"
                        }
                    ],
                    "summary": "Test summary",
                    "recommendations": ["Test recommendation"]
                })
            }
        ]
    }
    
    mock_bedrock.invoke_model.return_value = {
        "body": MagicMock(read=lambda: json.dumps(mock_response_body).encode())
    }
    
    engine = AIAnalysisEngine(mock_bedrock)
    
    # Create test sessions
    sessions = [
        create_test_session("s1", 0.3),
        create_test_session("s2", 0.4),
        create_test_session("s3", 0.35)
    ]
    
    # Analyze patterns
    result = engine.analyze_patterns(sessions)
    
    assert isinstance(result, AnalysisResult)
    assert len(result.patterns) == 1
    assert result.patterns[0].pattern == "Test pattern"
    assert result.summary == "Test summary"
    
    # Verify Bedrock was called
    assert mock_bedrock.invoke_model.called
    
    print("✓ Pattern analysis with mock test passed")


def test_generate_prompt_improvements_with_mock():
    """Test prompt improvement generation with mocked Bedrock response"""
    mock_bedrock = create_mock_bedrock_client()
    
    # Mock Bedrock response
    mock_response_body = {
        "content": [
            {
                "text": json.dumps({
                    "improvedPrompt": "Improved prompt text",
                    "changes": [
                        {
                            "section": "Instructions",
                            "reasoning": "Clarify expectations",
                            "impact": "Better performance"
                        }
                    ]
                })
            }
        ]
    }
    
    mock_bedrock.invoke_model.return_value = {
        "body": MagicMock(read=lambda: json.dumps(mock_response_body).encode())
    }
    
    engine = AIAnalysisEngine(mock_bedrock)
    
    # Create mock analysis result
    analysis = AnalysisResult(
        analysis_id="test-analysis",
        patterns=[
            Pattern(
                pattern="Test pattern",
                frequency=5,
                affected_sessions=["s1", "s2"],
                evidence="Test evidence"
            )
        ],
        summary="Test summary",
        recommendations=["Test recommendation"]
    )
    
    # Generate improvements
    result = engine.generate_prompt_improvements("Original prompt", analysis)
    
    assert isinstance(result, PromptImprovement)
    assert result.original_prompt == "Original prompt"
    assert result.improved_prompt == "Improved prompt text"
    assert len(result.changes) == 1
    
    # Verify Bedrock was called
    assert mock_bedrock.invoke_model.called
    
    print("✓ Prompt improvement generation with mock test passed")


def test_empty_sessions_handling():
    """Test that engine handles empty session list gracefully"""
    mock_bedrock = create_mock_bedrock_client()
    engine = AIAnalysisEngine(mock_bedrock)
    
    result = engine.analyze_patterns([])
    
    assert isinstance(result, AnalysisResult)
    assert len(result.patterns) == 0
    assert result.summary == "No sessions provided for analysis"
    
    print("✓ Empty sessions handling test passed")


if __name__ == "__main__":
    print("Running AI Engine tests...\n")
    
    test_ai_engine_initialization()
    test_format_sessions_for_analysis()
    test_parse_analysis_result()
    test_parse_prompt_improvement()
    test_analyze_patterns_with_mock()
    test_generate_prompt_improvements_with_mock()
    test_empty_sessions_handling()
    
    print("\n✅ All AI Engine tests passed!")
