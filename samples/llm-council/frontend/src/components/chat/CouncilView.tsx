/**
 * CouncilView Component
 *
 * Displays the 3-stage LLM Council deliberation process:
 * - Stage 1: Individual model responses in tabs
 * - Stage 2: Rankings and aggregate scores
 * - Stage 3: Final synthesized answer
 */

import { useState } from "react"
import { CouncilData } from "./types"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

interface CouncilViewProps {
  councilData: CouncilData
}

/**
 * Extract a short display name from a Bedrock model ID.
 * e.g. "us.anthropic.claude-sonnet-4-20250514-v1:0" -> "claude-sonnet-4"
 */
function getModelShortName(modelId: string): string {
  if (modelId.includes(".")) {
    const namePart = modelId.split(".").pop() || modelId
    const withoutVersion = namePart.split(":")[0]
    const parts = withoutVersion.split("-")
    const filtered = parts.filter(p => !/^\d{6,}$/.test(p) && p !== "v1")
    return filtered.join("-")
  }
  return modelId
}

export function CouncilView({ councilData }: CouncilViewProps) {
  const [activeTab, setActiveTab] = useState("0")

  // Show status message if we're still processing
  if (councilData.statusMessage) {
    return (
      <div className="p-4 bg-blue-50 rounded-lg border border-blue-200">
        <div className="flex items-center gap-2">
          <div className="animate-spin h-4 w-4 border-2 border-blue-600 border-t-transparent rounded-full" />
          <span className="text-sm text-blue-800">{councilData.statusMessage}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Stage 1: Individual Responses */}
      {councilData.stage1 && councilData.stage1.length > 0 && (
        <Card className="p-4">
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <Badge variant="outline">Stage 1</Badge>
            Individual Council Member Responses
          </h3>
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="grid w-full" style={{ gridTemplateColumns: `repeat(${councilData.stage1.length}, 1fr)` }}>
              {councilData.stage1.map((response, idx) => (
                <TabsTrigger key={idx} value={idx.toString()}>
                  {getModelShortName(response.model)}
                </TabsTrigger>
              ))}
            </TabsList>
            {councilData.stage1.map((response, idx) => (
              <TabsContent key={idx} value={idx.toString()} className="mt-3">
                <div className="text-sm whitespace-pre-wrap bg-gray-50 p-3 rounded border">
                  {response.response}
                </div>
                <div className="text-xs text-gray-500 mt-2">
                  Model: {response.model}
                </div>
              </TabsContent>
            ))}
          </Tabs>
        </Card>
      )}

      {/* Stage 2: Rankings */}
      {councilData.stage2 && councilData.stage2_metadata && (
        <Card className="p-4">
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <Badge variant="outline">Stage 2</Badge>
            Peer Rankings & Scores
          </h3>

          {/* Aggregate Scores */}
          <div className="mb-4">
            <h4 className="text-xs font-medium text-gray-700 mb-2">Aggregate Scores</h4>
            <div className="space-y-2">
              {(councilData.stage2_metadata.aggregate_rankings ?? [])
                .sort((a, b) => a.average_rank - b.average_rank)
                .map((score, idx) => {
                  const isWinner = idx === 0
                  return (
                    <div
                      key={idx}
                      className={`flex items-center justify-between p-2 rounded ${
                        isWinner ? 'bg-green-50 border border-green-200' : 'bg-gray-50'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">
                          {getModelShortName(score.model)}
                        </span>
                        {isWinner && (
                          <Badge variant="default" className="text-xs">Winner</Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-4 text-sm">
                        <span className="text-gray-600">
                          Avg Rank: <span className="font-medium">{score.average_rank.toFixed(2)}</span>
                        </span>
                        <span className="text-gray-600">
                          Ranked by: <span className="font-medium">{score.rankings_count}</span>
                        </span>
                      </div>
                    </div>
                  )
                })}
            </div>
          </div>

          {/* Individual Rankings */}
          <div>
            <h4 className="text-xs font-medium text-gray-700 mb-2">Individual Rankings</h4>
            <div className="space-y-2">
              {councilData.stage2.map((ranking, idx) => (
                <div key={idx} className="text-xs bg-gray-50 p-2 rounded">
                  <div className="font-medium mb-1">
                    {getModelShortName(ranking.model)}
                  </div>
                  <div className="flex gap-2 flex-wrap">
                    {ranking.parsed_ranking.map((label, ridx) => (
                      <span key={ridx} className="text-gray-600">
                        #{ridx + 1}: {label}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </Card>
      )}

      {/* Stage 3: Final Synthesis */}
      {councilData.stage3 && (
        <Card className="p-4">
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <Badge variant="outline">Stage 3</Badge>
            Chairman's Final Synthesis
          </h3>
          <div className="text-sm whitespace-pre-wrap bg-gradient-to-br from-blue-50 to-indigo-50 p-4 rounded border border-blue-200">
            {councilData.stage3.response}
          </div>
          <div className="text-xs text-gray-500 mt-2">
            Chairman: {getModelShortName(councilData.stage3.model)}
          </div>
        </Card>
      )}
    </div>
  )
}
