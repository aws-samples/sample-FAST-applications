"use client"

import { useEffect, useId, useRef, useState } from "react"
import type { Mermaid } from "mermaid"
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch"
import { Copy, Check, Maximize2, ZoomIn, ZoomOut, RotateCcw } from "lucide-react"
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog"

// Lazily loaded singleton. Mermaid is a large dependency (hundreds of KB), so it
// is only fetched the first time a diagram actually needs to render — chats with
// no diagrams never pay the cost.
let mermaidPromise: Promise<Mermaid> | null = null

function loadMermaid(): Promise<Mermaid> {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then(mod => {
      const mermaid = mod.default
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "neutral",
        fontFamily: "inherit",
      })
      return mermaid
    })
  }
  return mermaidPromise
}

function SourceFallback({ code, label }: { code: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = () => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <div className="my-2 rounded-md overflow-hidden border border-gray-300 bg-white">
      <div className="flex items-center justify-between px-3 py-1 bg-gray-100 border-b border-gray-300">
        <span className="text-xs text-gray-500">{label}</span>
        <button
          onClick={handleCopy}
          className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
          aria-label="Copy diagram source"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
      <pre className="m-0 p-3 text-xs overflow-x-auto font-mono text-gray-700 whitespace-pre">
        {code}
      </pre>
    </div>
  )
}

// Full-screen, zoomable/pannable view of a rendered diagram. Opened by clicking
// the inline diagram — complex architecture diagrams are unreadable at chat width.
function MermaidLightbox({
  svg,
  open,
  onOpenChange,
}: {
  svg: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton
        className="h-[90vh] w-[95vw] max-w-[95vw] gap-0 p-0 sm:max-w-[95vw]"
      >
        <DialogTitle className="sr-only">Diagram</DialogTitle>
        <DialogDescription className="sr-only">
          Zoomable and pannable view of the diagram. Scroll to zoom, drag to pan.
        </DialogDescription>
        <TransformWrapper
          initialScale={1}
          minScale={0.2}
          maxScale={8}
          centerOnInit
          limitToBounds={false}
          wheel={{ step: 0.15 }}
          doubleClick={{ mode: "reset" }}
        >
          {({ zoomIn, zoomOut, resetTransform }) => (
            <>
              <div className="absolute top-3 left-3 z-10 flex gap-1">
                <button
                  onClick={() => zoomIn()}
                  className="rounded-md border border-gray-300 bg-white/90 p-1.5 text-gray-600 shadow-sm hover:bg-gray-100"
                  aria-label="Zoom in"
                >
                  <ZoomIn size={16} />
                </button>
                <button
                  onClick={() => zoomOut()}
                  className="rounded-md border border-gray-300 bg-white/90 p-1.5 text-gray-600 shadow-sm hover:bg-gray-100"
                  aria-label="Zoom out"
                >
                  <ZoomOut size={16} />
                </button>
                <button
                  onClick={() => resetTransform()}
                  className="rounded-md border border-gray-300 bg-white/90 p-1.5 text-gray-600 shadow-sm hover:bg-gray-100"
                  aria-label="Reset zoom"
                >
                  <RotateCcw size={16} />
                </button>
              </div>
              <TransformComponent
                wrapperClass="!h-full !w-full"
                contentClass="!h-full !w-full items-center justify-center"
              >
                {/* Fit the whole diagram inside the modal at scale 1 so the
								    bottom is never clipped on open; the SVG is constrained to
								    the viewport box and zoom/pan takes over from there. */}
                <div
                  className="flex h-[90vh] w-[95vw] items-center justify-center p-6 [&_svg]:max-h-full [&_svg]:max-w-full [&_svg]:object-contain"
                  dangerouslySetInnerHTML={{ __html: svg }}
                />
              </TransformComponent>
            </>
          )}
        </TransformWrapper>
      </DialogContent>
    </Dialog>
  )
}

export function MermaidDiagram({ code }: { code: string }) {
  // useId yields a stable, unique id per component instance (SSR-safe). Strip the
  // colons React 19 emits — they are invalid in CSS/SVG ids that mermaid injects.
  const reactId = useId().replace(/:/g, "")
  const diagramId = `mermaid-${reactId}`

  const [svg, setSvg] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [lightboxOpen, setLightboxOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    const source = code.trim()
    if (!source) {
      setSvg(null)
      setFailed(false)
      return
    }

    loadMermaid()
      .then(async mermaid => {
        // During streaming the diagram source arrives token by token and is often
        // syntactically incomplete. parse() with suppressErrors validates without
        // throwing, so we simply wait for a later (complete) render pass instead of
        // surfacing a transient error.
        const valid = await mermaid.parse(source, { suppressErrors: true })
        if (cancelled) return
        if (!valid) {
          setFailed(true)
          return
        }
        const { svg: rendered } = await mermaid.render(diagramId, source)
        if (cancelled) return
        setSvg(rendered)
        setFailed(false)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })

    return () => {
      cancelled = true
    }
  }, [code, diagramId])

  // Could not render (genuinely invalid syntax, or still mid-stream): fall back to
  // the raw source so the user never sees a broken diagram or a crash.
  if (failed || (svg === null && code.trim())) {
    return <SourceFallback code={code} label={failed ? "mermaid (rendering failed)" : "mermaid"} />
  }
  if (svg === null) return null

  return (
    <>
      <div className="group relative my-2 rounded-md border border-gray-200 bg-white">
        <button
          type="button"
          onClick={() => setLightboxOpen(true)}
          className="absolute top-2 right-2 z-10 rounded-md border border-gray-300 bg-white/90 p-1.5 text-gray-500 opacity-0 shadow-sm transition-opacity hover:bg-gray-100 hover:text-gray-700 group-hover:opacity-100"
          aria-label="Expand diagram"
          title="Expand"
        >
          <Maximize2 size={14} />
        </button>
        <div
          ref={containerRef}
          onClick={() => setLightboxOpen(true)}
          className="flex cursor-zoom-in justify-center overflow-x-auto p-3 [&_svg]:h-auto [&_svg]:max-w-full"
          // Mermaid output is sanitized internally (securityLevel: "strict").
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
      <MermaidLightbox svg={svg} open={lightboxOpen} onOpenChange={setLightboxOpen} />
    </>
  )
}
