#!/usr/bin/env bash
#
# Export a draw.io (.drawio) diagram to an image using the draw.io desktop CLI.
#
# draw.io has no headless server build; the desktop app ships a CLI entry point
# that renders diagrams without opening a window. This wraps it with sensible
# defaults (high-res PNG, single page) and auto-detects the binary on macOS and
# Linux so callers do not need to know the platform-specific path.
#
# Usage:
#   scripts/drawio-export.sh [options] [<input.drawio>] [<output>]
#
# Options:
#   -p, --page-index <n>   Page to export, 0-based (default: 0). Ignored with --all-pages.
#   -a, --all-pages        Export every page (output must be a .pdf, or omit it).
#   -s, --scale <n>        Output scale factor (default: 3).
#   -b, --border <px>      Border width around the diagram (default: 20).
#   -f, --format <fmt>     Output format: png, pdf, svg, jpg (default: png).
#   -h, --help             Show this help.
#
# Arguments:
#   <input.drawio>  Source diagram. Default: docs/architecture-diagram/aws-specialist-agent-architecture.drawio
#   <output>        Destination image. Default: input path with the format extension.
#
# Examples:
#   scripts/drawio-export.sh
#   scripts/drawio-export.sh docs/architecture-diagram/aws-specialist-agent-architecture.drawio
#   scripts/drawio-export.sh -p 1 -s 2 my.drawio my-page2.png
#
set -euo pipefail

# Repo root is the parent of this script's directory, so default paths resolve
# regardless of the caller's working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEFAULT_INPUT="${REPO_ROOT}/docs/architecture-diagram/aws-specialist-agent-architecture.drawio"

page_index=0
all_pages=0
scale=3
border=20
format=png
input=""
output=""

usage() {
  # Print the leading comment block (the lines after the shebang) as help text.
  sed -n '3,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p;}' "${BASH_SOURCE[0]}"
}

die() {
  echo "error: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--page-index) page_index="${2:?--page-index needs a value}"; shift 2 ;;
    -a|--all-pages)  all_pages=1; shift ;;
    -s|--scale)      scale="${2:?--scale needs a value}"; shift 2 ;;
    -b|--border)     border="${2:?--border needs a value}"; shift 2 ;;
    -f|--format)     format="${2:?--format needs a value}"; shift 2 ;;
    -h|--help)       usage; exit 0 ;;
    --)              shift; break ;;
    -*)              die "unknown option: $1 (use --help)" ;;
    *)               break ;;
  esac
done

# Remaining positional args: input then output.
[[ $# -ge 1 ]] && { input="$1"; shift; }
[[ $# -ge 1 ]] && { output="$1"; shift; }
[[ $# -gt 0 ]] && die "too many arguments (use --help)"

input="${input:-$DEFAULT_INPUT}"
[[ -f "$input" ]] || die "input not found: $input"

# Default the output next to the input, swapping in the chosen format extension.
if [[ -z "$output" ]]; then
  output="${input%.*}.${format}"
fi

# Locate the draw.io CLI: PATH first (Linux/custom installs), then the macOS app
# bundle, where the binary is not on PATH by default.
if command -v drawio >/dev/null 2>&1; then
  DRAWIO="$(command -v drawio)"
elif [[ -x "/Applications/draw.io.app/Contents/MacOS/draw.io" ]]; then
  DRAWIO="/Applications/draw.io.app/Contents/MacOS/draw.io"
else
  die "draw.io CLI not found. Install it with: brew install --cask drawio"
fi

args=(--export --format "$format" --scale "$scale" --border "$border" --output "$output")
if [[ "$all_pages" -eq 1 ]]; then
  args+=(--all-pages)
else
  args+=(--page-index "$page_index")
fi

echo "exporting: $input -> $output (format=$format scale=$scale border=$border $([[ $all_pages -eq 1 ]] && echo all-pages || echo page-index=$page_index))" >&2
"$DRAWIO" "${args[@]}" "$input"
echo "done: $output" >&2
