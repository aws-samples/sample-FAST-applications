import { X } from "lucide-react"
import { FileUpload } from "@/components/upload/FileUpload"

interface KnowledgeBasePanelProps {
  onClose: () => void
}

export default function KnowledgeBasePanel({ onClose }: KnowledgeBasePanelProps) {
  return (
    <div className="fixed top-20 right-6 w-96 bg-white rounded-lg shadow-2xl p-6 z-50">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-xl font-bold text-gray-800">Knowledge Base</h3>
        <button onClick={onClose} className="hover:bg-gray-100 rounded p-1">
          <X className="h-5 w-5" />
        </button>
      </div>
      <p className="text-sm text-gray-600 mb-4">
        Upload restaurant documents to enhance the assistant&apos;s knowledge
      </p>
      <FileUpload />
    </div>
  )
}
