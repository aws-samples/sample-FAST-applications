#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Basic functionality validation for Evaluation API
This script validates that the core components work correctly
"""

import sys
from datetime import datetime, timedelta

# Import only the models module (no external dependencies)
from models import EvaluationResult, Session, SessionStatus, Span, Trace


def test_data_models():
    """Test that data models can be instantiated and serialized"""
    print("Testing data models...")

    # Test Span
    span = Span(
        span_id="span-123",
        trace_id="trace-456",
        parent_span_id=None,
        name="test-span",
        start_time=datetime.utcnow(),
        end_time=datetime.utcnow() + timedelta(seconds=1),
        attributes={"key": "value"},
        status="OK",
    )

    assert span.duration_ms >= 1000, "Span duration should be at least 1000ms"
    span_dict = span.to_dict()
    assert "spanId" in span_dict, "Span dict should have spanId"
    assert "durationMs" in span_dict, "Span dict should have durationMs"
    print("✓ Span model works correctly")

    # Test Trace
    trace = Trace(
        trace_id="trace-456",
        spans=[span],
        start_time=span.start_time,
        end_time=span.end_time,
    )

    assert trace.root_span == span, "Root span should be the span with no parent"
    assert trace.duration_ms >= 1000, "Trace duration should be at least 1000ms"
    trace_dict = trace.to_dict()
    assert "traceId" in trace_dict, "Trace dict should have traceId"
    assert len(trace_dict["spans"]) == 1, "Trace should have 1 span"
    print("✓ Trace model works correctly")

    # Test EvaluationResult
    evaluation = EvaluationResult(
        evaluation_id="eval-789",
        session_id="session-123",
        score=0.85,
        criteria={"accuracy": 0.9, "relevance": 0.8},
        feedback="Good performance",
    )

    eval_dict = evaluation.to_dict()
    assert eval_dict["score"] == 0.85, "Evaluation score should be preserved"
    assert "criteria" in eval_dict, "Evaluation should have criteria"
    print("✓ EvaluationResult model works correctly")

    # Test Session
    session = Session(
        session_id="session-123",
        timestamp=datetime.utcnow(),
        traces=[trace],
        evaluation=evaluation,
        status=SessionStatus.COMPLETED,
        metadata={"user": "test"},
    )

    assert session.score == 0.85, "Session score should match evaluation score"
    assert session.trace_count == 1, "Session should have 1 trace"
    assert session.span_count == 1, "Session should have 1 span"
    session_dict = session.to_dict()
    assert "sessionId" in session_dict, "Session dict should have sessionId"
    assert session_dict["status"] == "completed", "Session status should be serialized"
    print("✓ Session model works correctly")

    print("✅ All data model tests passed!\n")


def main():
    """Run all validation tests"""
    print("=" * 60)
    print("Evaluation API - Basic Functionality Validation")
    print("=" * 60)
    print()

    try:
        test_data_models()

        print("=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print()
        print("The basic API functionality is working correctly.")
        print("Core components validated:")
        print("  • Data models (Span, Trace, Session, EvaluationResult)")
        print("  • Model serialization to dictionaries")
        print("  • Session status enumeration")
        print("  • Property calculations (duration, counts)")
        print()
        return 0

    except AssertionError as e:
        print()
        print("=" * 60)
        print("❌ TEST FAILED!")
        print("=" * 60)
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print()
        print("=" * 60)
        print("❌ UNEXPECTED ERROR!")
        print("=" * 60)
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
