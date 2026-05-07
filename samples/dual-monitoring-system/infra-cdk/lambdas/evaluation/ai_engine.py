# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AI Analysis Engine using Strands agents for pattern analysis and prompt improvement"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List

from aws_lambda_powertools import Logger
from models import (
    AnalysisResult,
    Pattern,
    PromptChange,
    PromptImprovement,
    Session,
)

logger = Logger()


class AIAnalysisEngine:
    """
    AI Analysis Engine using Strands agents for evaluation insights

    This engine uses Amazon Bedrock with Strands agents to:
    1. Analyze patterns in low-scoring evaluation sessions
    2. Generate system prompt improvements based on identified patterns
    """

    def __init__(self, bedrock_client: Any):
        """
        Initialize AI Analysis Engine

        Args:
            bedrock_client: Boto3 Bedrock Runtime client
        """
        self.bedrock = bedrock_client
        # Use Claude Sonnet 4.5
        self.model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

        # Agent instructions for pattern analysis
        self.pattern_agent_instructions = """
You are an expert at analyzing agent evaluation data to identify patterns and root causes of poor performance.

Your task is to analyze low-scoring agent sessions and identify common patterns by examining:
- Trace structures and span hierarchies
- Error patterns and failure modes
- Timing and performance issues
- Common attributes across failed sessions
- Evaluation criteria that consistently score low

For each pattern you identify:
1. Describe the pattern clearly and specifically
2. Count how frequently it occurs
3. List the session IDs where it appears
4. Provide concrete evidence from the traces/spans

Provide actionable insights that can guide system prompt improvements.
Format your response as JSON with this structure:
{
  "patterns": [
    {
      "pattern": "description of pattern",
      "frequency": number,
      "affectedSessions": ["sessionId1", "sessionId2"],
      "evidence": "specific evidence from traces"
    }
  ],
  "summary": "overall summary of findings",
  "recommendations": ["recommendation 1", "recommendation 2"]
}
"""

        # Agent instructions for prompt improvement
        self.prompt_agent_instructions = """
You are an expert at improving system prompts for AI agents based on performance analysis.

Your task is to generate specific, actionable improvements to a system prompt based on identified failure patterns.

For each improvement:
1. Identify which section of the prompt needs modification
2. Explain the reasoning behind the change
3. Describe the expected impact on agent performance
4. Provide the improved prompt text

Focus on:
- Clarifying ambiguous instructions
- Adding missing constraints or guidelines
- Improving error handling instructions
- Enhancing reasoning guidance
- Addressing specific failure patterns identified in the analysis

Format your response as JSON with this structure:
{
  "improvedPrompt": "the complete improved prompt text",
  "changes": [
    {
      "section": "section name or description",
      "reasoning": "why this change addresses the identified issues",
      "impact": "expected improvement in agent performance"
    }
  ]
}
"""

    def _invoke_bedrock(
        self, system_prompt: str, user_message: str, max_tokens: int = 4096
    ) -> str:
        """
        Invoke Bedrock model with system prompt and user message

        Args:
            system_prompt: System instructions for the model
            user_message: User message/query
            max_tokens: Maximum tokens in response

        Returns:
            Model response text

        Raises:
            Exception: If Bedrock invocation fails
        """
        try:
            # Prepare request body for Claude
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
                "temperature": 0.7,
            }

            logger.info(f"Invoking Bedrock model: {self.model_id}")

            # Invoke model
            response = self.bedrock.invoke_model(
                modelId=self.model_id, body=json.dumps(request_body)
            )

            # Parse response
            response_body = json.loads(response["body"].read())

            # Extract text from Claude response
            if "content" in response_body and len(response_body["content"]) > 0:
                return response_body["content"][0]["text"]

            raise ValueError("No content in Bedrock response")

        except Exception as e:
            logger.error(f"Bedrock invocation failed: {str(e)}", exc_info=True)
            raise

    def _format_sessions_for_analysis(self, sessions: List[Session]) -> str:
        """
        Format session data for AI analysis

        Args:
            sessions: List of sessions to analyze

        Returns:
            Formatted string representation of sessions (truncated to avoid token limits)
        """
        formatted_data = []

        # Limit to first 20 sessions to avoid token overflow
        for session in sessions[:20]:
            session_data = {
                "sessionId": session.session_id,
                "timestamp": session.timestamp.isoformat(),
                "score": session.score,
                "status": session.status.value,
                "traceCount": session.trace_count,
                "spanCount": session.span_count,
                "traces": [],
            }

            # Include trace and span details (limit to first 5 traces per session)
            for trace in session.traces[:5]:
                trace_data = {
                    "traceId": trace.trace_id,
                    "durationMs": trace.duration_ms,
                    "spans": [],
                }

                # Limit to first 20 spans per trace
                for span in trace.spans[:20]:
                    # Truncate large attribute values
                    truncated_attributes = {}
                    for key, value in (span.attributes or {}).items():
                        if isinstance(value, str) and len(value) > 200:
                            truncated_attributes[key] = value[:200] + "..."
                        else:
                            truncated_attributes[key] = value

                    span_data = {
                        "spanId": span.span_id,
                        "parentSpanId": span.parent_span_id,
                        "name": span.name,
                        "durationMs": span.duration_ms,
                        "status": span.status,
                        "attributes": truncated_attributes,
                    }
                    trace_data["spans"].append(span_data)

                session_data["traces"].append(trace_data)

            # Include evaluation details if available
            if session.evaluation:
                # Truncate feedback if too long
                feedback = session.evaluation.feedback
                if feedback and len(feedback) > 500:
                    feedback = feedback[:500] + "..."

                session_data["evaluation"] = {
                    "score": session.evaluation.score,
                    "criteria": session.evaluation.criteria,
                    "feedback": feedback,
                }

            formatted_data.append(session_data)

        return json.dumps(formatted_data, indent=2)

    def _parse_analysis_result(
        self, response_text: str, sessions: List[Session]
    ) -> AnalysisResult:
        """
        Parse AI analysis response into AnalysisResult object

        Args:
            response_text: Raw response from AI model
            sessions: Original sessions that were analyzed

        Returns:
            Structured AnalysisResult object
        """
        try:
            # Try to extract JSON from response
            # The model might wrap JSON in markdown code blocks
            response_text = response_text.strip()

            # Remove markdown code blocks if present
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            response_text = response_text.strip()

            # Parse JSON
            data = json.loads(response_text)

            # Extract patterns
            patterns = []
            for pattern_data in data.get("patterns", []):
                pattern = Pattern(
                    pattern=pattern_data.get("pattern", ""),
                    frequency=pattern_data.get("frequency", 0),
                    affected_sessions=pattern_data.get("affectedSessions", []),
                    evidence=pattern_data.get("evidence", ""),
                )
                patterns.append(pattern)

            # Create AnalysisResult
            result = AnalysisResult(
                analysis_id=str(uuid.uuid4()),
                patterns=patterns,
                summary=data.get("summary", ""),
                recommendations=data.get("recommendations", []),
                timestamp=datetime.utcnow(),
            )

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse analysis response as JSON: {e}")
            logger.debug(f"Response text: {response_text}")

            # Return a fallback result
            return AnalysisResult(
                analysis_id=str(uuid.uuid4()),
                patterns=[],
                summary="Failed to parse AI analysis response",
                recommendations=["Review the raw analysis output for insights"],
                timestamp=datetime.utcnow(),
            )

    def _parse_prompt_improvement(
        self, response_text: str, original_prompt: str
    ) -> PromptImprovement:
        """
        Parse AI prompt improvement response into PromptImprovement object

        Args:
            response_text: Raw response from AI model
            original_prompt: Original system prompt

        Returns:
            Structured PromptImprovement object
        """
        try:
            # Try to extract JSON from response
            response_text = response_text.strip()

            # Remove markdown code blocks if present
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            response_text = response_text.strip()

            # Parse JSON
            data = json.loads(response_text)

            # Extract changes
            changes = []
            for change_data in data.get("changes", []):
                change = PromptChange(
                    section=change_data.get("section", ""),
                    reasoning=change_data.get("reasoning", ""),
                    impact=change_data.get("impact", ""),
                )
                changes.append(change)

            # Create PromptImprovement
            improvement = PromptImprovement(
                improvement_id=str(uuid.uuid4()),
                original_prompt=original_prompt,
                improved_prompt=data.get("improvedPrompt", original_prompt),
                changes=changes,
                timestamp=datetime.utcnow(),
            )

            return improvement

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse prompt improvement response as JSON: {e}")
            logger.debug(f"Response text: {response_text}")

            # Return a fallback result
            return PromptImprovement(
                improvement_id=str(uuid.uuid4()),
                original_prompt=original_prompt,
                improved_prompt=original_prompt,
                changes=[
                    PromptChange(
                        section="Error",
                        reasoning="Failed to parse AI response",
                        impact="No improvements generated",
                    )
                ],
                timestamp=datetime.utcnow(),
            )

    def analyze_patterns(self, sessions: List[Session]) -> AnalysisResult:
        """
        Analyze patterns across low-scoring sessions

        Args:
            sessions: List of sessions to analyze (should be pre-filtered by score)

        Returns:
            AnalysisResult with identified patterns and recommendations

        Raises:
            Exception: If analysis fails
        """
        logger.info(f"Analyzing patterns across {len(sessions)} sessions")

        if not sessions:
            return AnalysisResult(
                analysis_id=str(uuid.uuid4()),
                patterns=[],
                summary="No sessions provided for analysis",
                recommendations=[],
                timestamp=datetime.utcnow(),
            )

        # Log session data summary
        total_traces = sum(len(s.traces) for s in sessions)
        total_spans = sum(len(t.spans) for s in sessions for t in s.traces)
        logger.info(
            f"Sessions have {total_traces} total traces and {total_spans} total spans"
        )

        try:
            # Format session data for analysis
            formatted_sessions = self._format_sessions_for_analysis(sessions)

            logger.info(
                f"Formatted session data length: {len(formatted_sessions)} characters"
            )
            logger.info(
                f"First 500 chars of formatted data: {formatted_sessions[:500]}"
            )

            # Create user message
            user_message = f"""
Analyze the following {len(sessions)} low-scoring agent evaluation sessions and identify common patterns that may be causing poor performance.

Session Data:
{formatted_sessions}

Identify patterns in:
1. Trace structures and execution flows
2. Error patterns and failure modes
3. Performance issues (timing, duration anomalies)
4. Common span attributes or metadata
5. Evaluation criteria that consistently score low

Provide specific, actionable insights with evidence from the data.
"""

            # Invoke Bedrock with pattern analysis agent
            response = self._invoke_bedrock(
                system_prompt=self.pattern_agent_instructions,
                user_message=user_message,
                max_tokens=4096,
            )

            # Parse and return result
            result = self._parse_analysis_result(response, sessions)

            logger.info(
                f"Analysis complete: {len(result.patterns)} patterns identified"
            )

            return result

        except Exception as e:
            logger.error(f"Pattern analysis failed: {str(e)}", exc_info=True)
            raise

    def analyze_evaluation_results(
        self, evaluation_results: List[Dict[str, Any]]
    ) -> AnalysisResult:
        """
        Analyze patterns across low-scoring evaluation results

        Args:
            evaluation_results: List of evaluation result logs from CloudWatch

        Returns:
            AnalysisResult with identified patterns and recommendations

        Raises:
            Exception: If analysis fails
        """
        logger.info(
            f"Analyzing patterns across {len(evaluation_results)} evaluation results"
        )

        if not evaluation_results:
            return AnalysisResult(
                analysis_id=str(uuid.uuid4()),
                patterns=[],
                summary="No evaluation results provided for analysis",
                recommendations=[],
                timestamp=datetime.utcnow(),
            )

        try:
            # Format evaluation results for analysis
            formatted_data = []

            for result in evaluation_results:
                attributes = result.get("attributes", {})

                eval_data = {
                    "sessionId": attributes.get("session.id", "unknown"),
                    "traceId": result.get("traceId", "unknown"),
                    "evaluatorName": attributes.get(
                        "gen_ai.evaluation.name", "unknown"
                    ),
                    "score": attributes.get("gen_ai.evaluation.score.value", 0.0),
                    "scoreLabel": attributes.get("gen_ai.evaluation.score.label", ""),
                    "explanation": attributes.get("gen_ai.evaluation.explanation", ""),
                    "timestamp": result.get("timeUnixNano", 0),
                    "configName": attributes.get(
                        "aws.bedrock_agentcore.online_evaluation_config.name", ""
                    ),
                    "evaluationLevel": attributes.get(
                        "aws.bedrock_agentcore.evaluation_level", ""
                    ),
                }

                formatted_data.append(eval_data)

            formatted_json = json.dumps(formatted_data, indent=2)

            logger.info(
                f"Formatted evaluation data length: {len(formatted_json)} characters"
            )
            logger.info(f"First 500 chars: {formatted_json[:500]}")

            # Create user message
            user_message = f"""
Analyze the following {len(evaluation_results)} low-scoring agent evaluation results and identify common patterns that may be causing poor performance.

Evaluation Results:
{formatted_json}

Each evaluation includes:
- sessionId: The agent conversation session
- evaluatorName: Which evaluator assessed the response (e.g., Builtin.Correctness, Builtin.Helpfulness)
- score: The evaluation score (0.0-1.0)
- scoreLabel: Human-readable score label
- explanation: Detailed reasoning from the evaluator about why this score was given
- evaluationLevel: Whether this was evaluated at TRACE, TOOL_CALL, or SESSION level

Focus on:
1. Common failure patterns mentioned in the explanations
2. Which evaluators are consistently scoring low
3. Specific issues called out in the evaluation explanations
4. Patterns across sessions or traces

Provide specific, actionable insights with evidence from the evaluation explanations.
"""

            # Invoke Bedrock with pattern analysis agent
            response = self._invoke_bedrock(
                system_prompt=self.pattern_agent_instructions,
                user_message=user_message,
                max_tokens=4096,
            )

            # Parse and return result
            result = self._parse_analysis_result(response, [])

            logger.info(
                f"Evaluation analysis complete: {len(result.patterns)} patterns identified"
            )

            return result

        except Exception as e:
            logger.error(f"Evaluation analysis failed: {str(e)}", exc_info=True)
            raise

    def generate_prompt_improvements(
        self, current_prompt: str, analysis: AnalysisResult
    ) -> PromptImprovement:
        """
        Generate improved system prompt based on analysis results

        Args:
            current_prompt: Current system prompt text
            analysis: Analysis results with identified patterns

        Returns:
            PromptImprovement with suggested changes and reasoning

        Raises:
            Exception: If prompt improvement generation fails
        """
        logger.info(
            f"Generating prompt improvements based on {len(analysis.patterns)} patterns"
        )

        try:
            # Format analysis results for prompt improvement
            # Limit evidence to first 500 chars per pattern to avoid token overflow
            analysis_summary = {
                "summary": analysis.summary,
                "patterns": [
                    {
                        "pattern": p.pattern,
                        "frequency": p.frequency,
                        "evidence": p.evidence[:500] + "..."
                        if len(p.evidence) > 500
                        else p.evidence,
                    }
                    for p in analysis.patterns[:10]  # Limit to top 10 patterns
                ],
                "recommendations": analysis.recommendations[
                    :10
                ],  # Limit to top 10 recommendations
            }

            # Create user message
            user_message = f"""
Based on the following analysis of agent performance issues, generate an improved version of the system prompt.

Current System Prompt:
{current_prompt}

Performance Analysis:
{json.dumps(analysis_summary, indent=2)}

Generate an improved prompt that addresses the identified issues. For each change:
1. Specify which section or aspect of the prompt is being modified
2. Explain how the change addresses the identified patterns
3. Describe the expected impact on agent performance

Provide the complete improved prompt along with a detailed explanation of changes.
"""

            # Invoke Bedrock with prompt improvement agent
            response = self._invoke_bedrock(
                system_prompt=self.prompt_agent_instructions,
                user_message=user_message,
                max_tokens=8192,  # Larger token limit for full prompt generation
            )

            # Parse and return result
            improvement = self._parse_prompt_improvement(response, current_prompt)

            logger.info(
                f"Prompt improvement generated with {len(improvement.changes)} changes"
            )

            return improvement

        except Exception as e:
            logger.error(
                f"Prompt improvement generation failed: {str(e)}", exc_info=True
            )
            raise
