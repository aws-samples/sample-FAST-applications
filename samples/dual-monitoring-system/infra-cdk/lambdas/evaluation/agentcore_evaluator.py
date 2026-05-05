"""
AgentCore Evaluation Integration

Uses the bedrock-agentcore-starter-toolkit SDK for simplified evaluation management.
Follows the official AgentCore evaluation patterns from:
https://github.com/awslabs/amazon-bedrock-agentcore-samples
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from bedrock_agentcore_starter_toolkit import Evaluation
import json


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class AgentCoreEvaluator:
    """Client for AgentCore evaluation operations using the starter toolkit SDK"""
    
    # Built-in evaluator IDs from AgentCore
    BUILTIN_EVALUATORS = {
        'helpfulness': 'Builtin.Helpfulness',
        'correctness': 'Builtin.Correctness',
        'goal_success': 'Builtin.GoalSuccessRate',
        'faithfulness': 'Builtin.Faithfulness',
        'instruction_following': 'Builtin.InstructionFollowing',
        'context_relevance': 'Builtin.ContextRelevance',
        'coherence': 'Builtin.Coherence',
        'conciseness': 'Builtin.Conciseness',
        'harmfulness': 'Builtin.Harmfulness',
        'maliciousness': 'Builtin.Maliciousness',
        'stereotyping': 'Builtin.Stereotyping',
        'refusal': 'Builtin.Refusal',
        'tool_selection_accuracy': 'Builtin.ToolSelectionAccuracy',
        'tool_parameter_accuracy': 'Builtin.ToolParameterAccuracy',
        'response_relevance': 'Builtin.ResponseRelevance'
    }
    
    def __init__(self, region: Optional[str] = None):
        """
        Initialize AgentCore evaluation client
        
        Args:
            region: AWS region (defaults to environment variable)
        """
        self.region = region or os.environ.get('AWS_REGION', 'us-east-1')
        self.eval_client = Evaluation(region=self.region)
        self.runtime_arn = os.environ.get('RUNTIME_ARN', '')
        self.agent_id = self._extract_agent_id_from_arn(self.runtime_arn)
        
        logger.info(f"Initialized AgentCore evaluator for agent: {self.agent_id}")
    
    def _extract_agent_id_from_arn(self, arn: str) -> str:
        """Extract agent ID from runtime ARN"""
        if not arn:
            return ''
        # ARN format: arn:aws:bedrock-agentcore:region:account:runtime/agent_name-ID
        parts = arn.split('/')
        return parts[-1] if parts else ''
    
    def list_builtin_evaluators(self) -> List[Dict[str, Any]]:
        """
        List all built-in evaluators
        
        Returns:
            List of built-in evaluator information
        """
        evaluators = []
        for key, evaluator_id in self.BUILTIN_EVALUATORS.items():
            # Determine level based on evaluator type
            if 'goal' in key.lower():
                level = 'SESSION'
            elif 'tool' in key.lower():
                level = 'TOOL_CALL'
            else:
                level = 'TRACE'
            
            evaluators.append({
                'id': evaluator_id,
                'name': key,
                'level': level,
                'builtin': True
            })
        
        return evaluators
    
    def list_custom_evaluators(self) -> List[Dict[str, Any]]:
        """
        List all custom evaluators
        
        Returns:
            List of custom evaluator information
        """
        try:
            # Use the SDK to list custom evaluators
            response = self.eval_client.list_evaluators()
            
            # Filter for custom evaluators (non-builtin)
            custom_evaluators = []
            for evaluator in response.get('evaluatorSummaries', []):
                evaluator_id = evaluator.get('evaluatorId', '')
                # Custom evaluators don't start with "Builtin."
                if not evaluator_id.startswith('Builtin.'):
                    custom_evaluators.append({
                        'id': evaluator_id,
                        'name': evaluator.get('evaluatorName', evaluator_id),
                        'level': evaluator.get('evaluationLevel', 'TRACE'),
                        'builtin': False
                    })
            
            return custom_evaluators
            
        except Exception as e:
            logger.warning(f"Failed to list custom evaluators: {e}")
            return []
    
    def create_online_evaluation(
        self,
        config_name: str,
        evaluator_ids: List[str],
        sampling_rate: float = 10.0,
        description: str = "",
        enable_on_create: bool = True
    ) -> Dict[str, Any]:
        """
        Create an online evaluation configuration using the SDK
        
        Args:
            config_name: Name for the evaluation configuration
            evaluator_ids: List of evaluator IDs (built-in or custom, max 10)
            sampling_rate: Sampling rate percentage (0.01-100.0, default 10.0)
            description: Configuration description
            enable_on_create: Whether to enable evaluation immediately (default True)
            
        Returns:
            Evaluation configuration details
        """
        if not self.agent_id:
            raise ValueError("Agent ID not available. RUNTIME_ARN environment variable may be missing.")
        
        if len(evaluator_ids) > 10:
            raise ValueError("Maximum 10 evaluators allowed per configuration")
        
        if not 0.01 <= sampling_rate <= 100.0:
            raise ValueError("Sampling rate must be between 0.01 and 100.0")
        
        try:
            logger.info(f"Creating online evaluation config: {config_name}")
            logger.info(f"Agent ID: {self.agent_id}")
            logger.info(f"Evaluators: {evaluator_ids}")
            logger.info(f"Sampling rate: {sampling_rate}%")
            
            # Use the SDK's create_online_config method
            response = self.eval_client.create_online_config(
                agent_id=self.agent_id,
                config_name=config_name,
                sampling_rate=sampling_rate,
                evaluator_list=evaluator_ids,
                config_description=description,
                auto_create_execution_role=True,  # SDK handles role creation
                enable_on_create=enable_on_create
            )
            
            config_id = response.get('onlineEvaluationConfigId', '')
            eval_arn = response.get('onlineEvaluationConfigArn', '')
            execution_status = response.get('executionStatus', 'UNKNOWN')
            status = response.get('status', 'UNKNOWN')
            
            logger.info(f"✓ Created online evaluation: {config_name} ({config_id})")
            
            return {
                'configId': config_id,
                'configName': config_name,
                'evaluationArn': eval_arn,
                'executionStatus': execution_status,
                'status': status,
                'samplingRate': sampling_rate,
                'evaluators': evaluator_ids,
                'alreadyExists': False
            }
            
        except Exception as e:
            error_msg = str(e)
            
            # Handle ConflictException - configuration already exists
            if 'ConflictException' in error_msg or 'already exists' in error_msg.lower():
                logger.info(f"Configuration {config_name} already exists, fetching existing config")
                
                # Try to find the existing configuration
                try:
                    configs = self.list_online_evaluations()
                    for config in configs:
                        if config.get('onlineEvaluationConfigName') == config_name:
                            return {
                                'configId': config.get('onlineEvaluationConfigId', ''),
                                'configName': config_name,
                                'evaluationArn': config.get('onlineEvaluationConfigArn', ''),
                                'executionStatus': config.get('executionStatus', 'UNKNOWN'),
                                'status': config.get('status', 'UNKNOWN'),
                                'samplingRate': sampling_rate,
                                'evaluators': evaluator_ids,
                                'alreadyExists': True,
                                'message': 'Evaluation configuration already exists'
                            }
                except Exception as list_error:
                    logger.warning(f"Could not list existing configs: {list_error}")
                
                # If we can't find it, return a generic response
                return {
                    'configName': config_name,
                    'alreadyExists': True,
                    'message': 'Evaluation configuration with this name already exists'
                }
            
            logger.error(f"Failed to create online evaluation: {e}")
            raise
    
    def get_online_evaluation(self, config_id: str) -> Dict[str, Any]:
        """
        Get details of an online evaluation configuration
        
        Args:
            config_id: Evaluation configuration ID
            
        Returns:
            Configuration details
        """
        try:
            response = self.eval_client.get_online_config(config_id=config_id)
            return response
        except Exception as e:
            logger.error(f"Failed to get online evaluation: {e}")
            raise
    
    def list_online_evaluations(self) -> List[Dict[str, Any]]:
        """
        List all online evaluation configurations
        
        Returns:
            List of online evaluation configurations
        """
        try:
            # SDK's list_online_configs doesn't take agent_id parameter
            response = self.eval_client.list_online_configs()
            
            logger.info(f"Raw SDK response: {response}")
            
            configs = response.get('onlineEvaluationConfigs', [])
            logger.info(f"Configs retrieved: {configs}")
            
            # Convert datetime objects to ISO format strings for JSON serialization
            serializable_configs = []
            for config in configs:
                if isinstance(config, dict):
                    serializable_config = {}
                    for key, value in config.items():
                        if isinstance(value, datetime):
                            serializable_config[key] = value.isoformat()
                        else:
                            serializable_config[key] = value
                    serializable_configs.append(serializable_config)
                else:
                    serializable_configs.append(config)
            
            logger.info(f"Returning {len(serializable_configs)} serializable configs")
            
            return serializable_configs
        except Exception as e:
            logger.error(f"Failed to list online evaluations: {e}", exc_info=True)
            return []
    
    def update_online_evaluation(
        self,
        config_id: str,
        execution_status: Optional[str] = None,
        sampling_rate: Optional[float] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update an existing online evaluation configuration
        
        Args:
            config_id: Evaluation configuration ID
            execution_status: ENABLED or DISABLED (optional)
            sampling_rate: New sampling rate (optional)
            description: New description (optional)
            
        Returns:
            Updated configuration details
        """
        try:
            kwargs = {'config_id': config_id}
            
            if execution_status:
                if execution_status not in ['ENABLED', 'DISABLED']:
                    raise ValueError("execution_status must be ENABLED or DISABLED")
                kwargs['execution_status'] = execution_status
            
            if sampling_rate is not None:
                if not 0.01 <= sampling_rate <= 100.0:
                    raise ValueError("Sampling rate must be between 0.01 and 100.0")
                kwargs['sampling_rate'] = sampling_rate
            
            if description:
                kwargs['description'] = description
            
            response = self.eval_client.update_online_config(**kwargs)
            
            logger.info(f"Updated online evaluation: {config_id}")
            
            return {
                'configId': config_id,
                'message': 'Successfully updated online evaluation configuration'
            }
            
        except Exception as e:
            logger.error(f"Failed to update online evaluation: {e}")
            raise
    
    def delete_online_evaluation(self, config_id: str) -> Dict[str, Any]:
        """
        Delete an online evaluation configuration
        
        Args:
            config_id: Evaluation configuration ID
            
        Returns:
            Deletion confirmation
        """
        try:
            self.eval_client.delete_online_config(config_id=config_id)
            
            logger.info(f"Deleted online evaluation: {config_id}")
            
            return {
                'configId': config_id,
                'message': 'Successfully deleted online evaluation configuration'
            }
            
        except Exception as e:
            logger.error(f"Failed to delete online evaluation: {e}")
            raise
    
    def get_evaluation_metrics(
        self,
        config_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """
        Get aggregated evaluation metrics from CloudWatch
        
        Args:
            config_id: Online evaluation configuration ID
            start_time: Start of time range
            end_time: End of time range
            
        Returns:
            Aggregated metrics
        """
        import boto3
        
        # Create a fresh CloudWatch client for each call to avoid caching
        cloudwatch_logs = boto3.client('logs', region_name=self.region)
        log_group = f"/aws/bedrock-agentcore/evaluations/results/{config_id}"
        
        try:
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)
            logger.info(f"GET_EVALUATION_METRICS for config: {config_id}")
            
            results = []
            next_token = None
            page_count = 0
            
            # Paginate through all results
            while True:
                page_count += 1
                params = {
                    'logGroupName': log_group,
                    'startTime': start_ms,
                    'endTime': end_ms,
                    'limit': 10000  # Max limit per request
                }
                
                if next_token:
                    params['nextToken'] = next_token
                
                response = cloudwatch_logs.filter_log_events(**params)
                
                events = response.get('events', [])
                logger.info(f"Query params: logGroupName={params['logGroupName']}")
                logger.info(f"Received {len(events)} events for config {config_id}")

                for event in events:
                    try:
                        log_data = json.loads(event['message'])
                        results.append(log_data)
                    except json.JSONDecodeError:
                        continue
                
                # Check for more pages
                next_token = response.get('nextToken')
                if not next_token:
                    break
            
            logger.info(f"Config {config_id}: Found {len(results)} evaluation results")
            
            # Log unique evaluators found
            unique_evaluators = set()
            for result in results:
                if 'attributes' in result and isinstance(result['attributes'], dict):
                    evaluator_name = result['attributes'].get('gen_ai.evaluation.name', 'unknown')
                    unique_evaluators.add(evaluator_name)
            
            logger.info(f"Config {config_id}: Unique evaluators: {unique_evaluators}")
            logger.info(f"=" * 80)
            
            if not results:
                return {
                    'totalEvaluations': 0,
                    'averageScore': 0.0,
                    'scoreDistribution': {},
                    'evaluatorMetrics': {}
                }
            
            # Calculate metrics
            total_score = 0.0
            score_distribution = {
                '0.0-0.2': 0,
                '0.2-0.4': 0,
                '0.4-0.6': 0,
                '0.6-0.8': 0,
                '0.8-1.0': 0
            }
            evaluator_metrics = {}
            
            for result in results:
                # Extract score from the correct field
                # The score is in attributes['gen_ai.evaluation.score.value']
                score = 0.0
                if 'attributes' in result and isinstance(result['attributes'], dict):
                    score = result['attributes'].get('gen_ai.evaluation.score.value', 0.0)
                else:
                    # Fallback to old fields
                    score = result.get('value', result.get('score', 0.0))
                
                total_score += score
                
                # Score distribution
                if score < 0.2:
                    score_distribution['0.0-0.2'] += 1
                elif score < 0.4:
                    score_distribution['0.2-0.4'] += 1
                elif score < 0.6:
                    score_distribution['0.4-0.6'] += 1
                elif score < 0.8:
                    score_distribution['0.6-0.8'] += 1
                else:
                    score_distribution['0.8-1.0'] += 1
                
                # Per-evaluator metrics
                # Extract evaluator name from attributes['gen_ai.evaluation.name']
                evaluator_id = 'unknown'
                if 'attributes' in result and isinstance(result['attributes'], dict):
                    evaluator_id = result['attributes'].get('gen_ai.evaluation.name', 'unknown')

                
                # logger.info(f"Extracted evaluator_id: {evaluator_id}, score: {score}")
                
                if evaluator_id not in evaluator_metrics:
                    evaluator_metrics[evaluator_id] = {
                        'count': 0,
                        'totalScore': 0.0,
                        'averageScore': 0.0
                    }
                
                evaluator_metrics[evaluator_id]['count'] += 1
                evaluator_metrics[evaluator_id]['totalScore'] += score
            
            # Calculate averages
            avg_score = total_score / len(results) if results else 0.0
            
            for evaluator_id in evaluator_metrics:
                metrics = evaluator_metrics[evaluator_id]
                metrics['averageScore'] = metrics['totalScore'] / metrics['count']
            return {
                'totalEvaluations': len(results),
                'averageScore': avg_score,
                'scoreDistribution': score_distribution,
                'evaluatorMetrics': evaluator_metrics
            }
            
        except cloudwatch_logs.exceptions.ResourceNotFoundException:
            logger.warning(f"Log group not found: {log_group}. No evaluations have run yet.")
            return {
                'totalEvaluations': 0,
                'averageScore': 0.0,
                'scoreDistribution': {},
                'evaluatorMetrics': {},
                'message': 'No evaluation results yet. Invoke your agent to generate evaluations.'
            }
        except Exception as e:
            logger.error(f"Failed to get evaluation metrics: {e}")
            return {
                'totalEvaluations': 0,
                'averageScore': 0.0,
                'scoreDistribution': {},
                'evaluatorMetrics': {},
                'error': str(e)
            }


    def setup_default_evaluation(
        self,
        config_name: str = "default_evaluation_",
        sampling_rate: float = 10.0,
        enable_on_create: bool = True
    ) -> Dict[str, Any]:
        """
        Set up default online evaluation with built-in evaluators
        
        Args:
            config_name: Name for the evaluation configuration
            sampling_rate: Sampling rate percentage (0.01-100.0, default 10.0)
            enable_on_create: Whether to enable immediately (default True)
            
        Returns:
            Configuration details
        """
        # Use key built-in evaluators
        evaluator_ids = [
            self.BUILTIN_EVALUATORS['helpfulness'],
            self.BUILTIN_EVALUATORS['correctness'],
            self.BUILTIN_EVALUATORS['goal_success']
        ]
        
        try:
            result = self.create_online_evaluation(
                config_name=config_name,
                evaluator_ids=evaluator_ids,
                sampling_rate=sampling_rate,
                description="Default evaluation configuration with helpfulness, correctness, and goal success",
                enable_on_create=enable_on_create
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to setup default evaluation: {e}")
            raise
