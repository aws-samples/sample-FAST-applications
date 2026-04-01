import { useState } from "react"
import { Upload, X, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { uploadFile, syncKnowledgeBase } from "@/services/uploadService"
import { useAuth } from "@/hooks/useAuth"

export function FileUpload() {
  const [uploading, setUploading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const { token } = useAuth()

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && files.length > 0) {
      setSelectedFiles(Array.from(files))
      setError(null)
      setSuccess(null)
    }
  }

  const removeFile = (index: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index))
  }

  const handleUpload = async () => {
    if (selectedFiles.length === 0 || !token) return

    setUploading(true)
    setError(null)
    setSuccess(null)

    let uploaded = 0
    const failed: string[] = []

    for (const file of selectedFiles) {
      try {
        await uploadFile(file, token)
        uploaded++
      } catch {
        failed.push(file.name)
      }
    }

    setUploading(false)
    setSelectedFiles([])

    if (failed.length > 0) {
      setError(`Failed to upload: ${failed.join(", ")}`)
    }
    if (uploaded > 0) {
      setSuccess(`Successfully uploaded ${uploaded} file${uploaded > 1 ? "s" : ""}`)
    }
  }

  const handleSync = async () => {
    if (!token) return

    setSyncing(true)
    setError(null)
    setSuccess(null)

    try {
      await syncKnowledgeBase(token)
      setSuccess("Syncing Knowledge Base with new files, may take a minute...")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed")
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="border rounded-lg p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Upload className="h-5 w-5" />
        <h3 className="font-semibold">Upload Restaurant Documents</h3>
      </div>

      <div className="space-y-2">
        <input
          type="file"
          onChange={handleFileSelect}
          accept=".pdf,.doc,.docx,.txt"
          className="hidden"
          disabled={uploading}
          id="file-upload"
          multiple
        />
        <label htmlFor="file-upload">
          <Button variant="outline" className="w-full" asChild disabled={uploading}>
            <span className="cursor-pointer">Choose Files</span>
          </Button>
        </label>

        {selectedFiles.length > 0 && (
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {selectedFiles.map((file, index) => (
              <div key={index} className="flex items-center justify-between p-2 bg-gray-50 rounded">
                <span className="text-sm truncate">{file.name}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => removeFile(index)}
                  disabled={uploading}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
        )}

        <Button
          onClick={handleUpload}
          disabled={selectedFiles.length === 0 || uploading}
          className="w-full"
        >
          {uploading
            ? "Uploading..."
            : `Upload${selectedFiles.length > 0 ? ` (${selectedFiles.length})` : ""}`}
        </Button>

        <Button onClick={handleSync} disabled={syncing} variant="outline" className="w-full">
          <RefreshCw className={`h-4 w-4 mr-2 ${syncing ? "animate-spin" : ""}`} />
          {syncing ? "Syncing..." : "Sync Knowledge Base"}
        </Button>

        {error && <p className="text-sm text-red-600">{error}</p>}
        {success && <p className="text-sm text-green-600">{success}</p>}
      </div>
    </div>
  )
}
