"use client"

import { FormEvent, KeyboardEvent, useRef, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Loader2Icon, Send } from "lucide-react"
import { ModelSelector } from "./ModelSelector"
import { SelectableModel } from "./types"

interface ChatInputProps {
  input: string
  setInput: (input: string) => void
  handleSubmit: (e: FormEvent) => void
  isLoading: boolean
  className?: string
  // Model picker wiring. Optional so the input renders even before
  // the model list loads; the selector hides itself when models is empty.
  models?: SelectableModel[]
  selectedModelKey?: string | null
  onModelChange?: (key: string) => void
}

export function ChatInput({
  input,
  setInput,
  handleSubmit,
  isLoading,
  className = "",
  models = [],
  selectedModelKey = null,
  onModelChange,
}: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-resize the textarea based on content
  useEffect(() => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = "0px"
      const scrollHeight = textarea.scrollHeight
      textarea.style.height = scrollHeight + "px"
    }
  }, [input])

  // Submit on Cmd+Enter (Mac) / Ctrl+Enter (Windows); plain Enter inserts a
  // newline. This avoids accidental sends while composing Japanese (IME), where
  // Enter is used to confirm a conversion: an Enter that confirms an IME
  // composition is ignored entirely so it neither sends nor adds a stray newline.
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter") return

    // Ignore the Enter that confirms an IME conversion (isComposing is true
    // mid-composition; keyCode 229 is the legacy signal for the same).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return

    if (e.metaKey || e.ctrlKey) {
      // Send on Cmd/Ctrl+Enter.
      e.preventDefault()
      if (input.trim()) {
        handleSubmit(e as unknown as FormEvent)
      }
    }
    // Plain Enter / Shift+Enter: let the textarea insert a newline (default).
  }

  return (
    <div className={`p-4 w-full ${className}`}>
      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-2 w-full bg-white rounded-lg shadow-lg border border-gray-200 p-3"
      >
        <Textarea
          ref={textareaRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type your message... (Cmd/Ctrl+Enter to send)"
          disabled={isLoading}
          className="flex-1 min-h-[40px] max-h-[200px] resize-none py-2"
          rows={1}
          autoFocus
        />

        <div className="flex items-center justify-between gap-2">
          {/* Model picker. Disabled mid-stream so the model cannot change during a turn. Hides itself until models load. */}
          <ModelSelector
            models={models}
            value={selectedModelKey}
            onChange={onModelChange ?? (() => {})}
            disabled={isLoading}
          />

          <Button type="submit" disabled={!input.trim() || isLoading} className="h-10">
            {isLoading ? (
              <>
                <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
                Thinking...
              </>
            ) : (
              <>
                <Send className="h-4 w-4 mr-2" />
                Send
              </>
            )}
          </Button>
        </div>
      </form>
    </div>
  )
}
