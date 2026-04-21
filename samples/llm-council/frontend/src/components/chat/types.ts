// Define message types
export type MessageRole = "user" | "assistant"

// Council-specific types
export interface CouncilStage1Response {
  model: string
  response: string
}

export interface CouncilStage2Ranking {
  model: string
  ranking: string
  parsed_ranking: string[]
}

export interface CouncilAggregateRanking {
  model: string
  average_rank: number
  rankings_count: number
}

export interface CouncilMetadata {
  label_to_model: Record<string, string>
  aggregate_rankings: CouncilAggregateRanking[]
}

export interface CouncilStage3Result {
  model: string
  response: string
}

export interface CouncilData {
  stage1?: CouncilStage1Response[]
  stage2?: CouncilStage2Ranking[]
  stage2_metadata?: CouncilMetadata
  stage3?: CouncilStage3Result
  currentStage?: number
  statusMessage?: string
}

export interface Message {
  role: MessageRole
  content: string
  timestamp: string
  councilData?: CouncilData
}

// Define chat session types
export interface ChatSession {
  id: string
  name: string
  history: Message[]
  startDate: string
  endDate: string
}
