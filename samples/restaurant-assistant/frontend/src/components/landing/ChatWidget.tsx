import { X, Maximize2, Minimize2 } from "lucide-react"
import { useState } from "react"
import { GlobalContextProvider } from "@/app/context/GlobalContext"
import ChatInterface from "@/components/chat/ChatInterface"

interface ChatWidgetProps {
  onClose: () => void
}

export default function ChatWidget({ onClose }: ChatWidgetProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  return (
    <div
      className={`fixed ${isExpanded ? "inset-4" : "bottom-6 right-6 w-96 h-[600px]"} bg-white rounded-lg shadow-2xl flex flex-col overflow-hidden z-50 transition-all duration-300`}
    >
      <div className="bg-orange-600 text-white p-4 flex justify-between items-center">
        <h3 className="font-semibold">Restaurant Helper</h3>
        <div className="flex gap-2">
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="hover:bg-orange-700 rounded p-1"
            title={isExpanded ? "Minimize" : "Maximize"}
          >
            {isExpanded ? <Minimize2 className="h-5 w-5" /> : <Maximize2 className="h-5 w-5" />}
          </button>
          <button onClick={onClose} className="hover:bg-orange-700 rounded p-1">
            <X className="h-5 w-5" />
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-hidden">
        <GlobalContextProvider>
          <ChatInterface embedded />
        </GlobalContextProvider>
      </div>
    </div>
  )
}
