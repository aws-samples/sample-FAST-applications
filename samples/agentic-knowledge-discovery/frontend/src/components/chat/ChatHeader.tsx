import { SidebarTrigger } from "@/components/ui/sidebar"

type ChatHeaderProps = {
  title?: string | undefined
  models: { id: string; label: string }[]
  modelId: string
  onModelChange: (id: string) => void
}

/**
 * Minimal top bar: sidebar toggle, app title, and a model selector. New Chat,
 * session history, and logout live in the sidebar (see ChatSidebar).
 */
export function ChatHeader({ title, models, modelId, onModelChange }: ChatHeaderProps) {
  return (
    <header className="flex items-center gap-2 p-4 border-b w-full">
      <SidebarTrigger className="text-gray-500" />
      <h1 className="text-xl font-bold">{title || "Knowledge Agent"}</h1>
      <label className="ml-auto flex items-center gap-2 text-xs text-gray-500">
        Model
        <select
          value={modelId}
          onChange={e => onModelChange(e.target.value)}
          className="rounded-md border border-gray-200 bg-white px-2 py-1 text-sm text-gray-700"
        >
          {models.map(m => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </label>
    </header>
  )
}
