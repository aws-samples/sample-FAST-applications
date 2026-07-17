"use client"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { SelectableModel } from "./types"

interface ModelSelectorProps {
  // The selectable models published via aws-exports.json.
  models: SelectableModel[]
  // Currently selected logical key (e.g. "opus-4.8"), or null before seeding.
  value: string | null
  // Called with the chosen logical key when the user picks a model.
  onChange: (key: string) => void
  // Disabled while a response is streaming, to avoid switching mid-turn.
  disabled?: boolean
}

/**
 * Compact model picker shown next to the chat input.
 *
 * Renders every model from the server-provided list; all of them are selectable
 * and work. The component only ever deals in the logical `key`; the physical
 * Bedrock id stays server-side.
 */
export function ModelSelector({ models, value, onChange, disabled = false }: ModelSelectorProps) {
  // Nothing to show until the model list has loaded.
  if (models.length === 0) {
    return null
  }

  return (
    <Select value={value ?? undefined} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger size="sm" className="w-[180px]" aria-label="Select model">
        <SelectValue placeholder="Select model" />
      </SelectTrigger>
      <SelectContent>
        {models.map(model => (
          <SelectItem key={model.key} value={model.key}>
            {model.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
