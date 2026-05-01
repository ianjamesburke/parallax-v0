#!/bin/sh
set -e

# ─── uv ──────────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "→ Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The uv installer updates shell rc files but not the current process.
    export PATH="$HOME/.local/bin:$PATH"
fi

# ─── Homebrew (macOS only) ────────────────────────────────────────────────────
if [ "$(uname)" = "Darwin" ] && ! command -v brew >/dev/null 2>&1; then
    echo "→ Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for the rest of this script (Apple Silicon vs Intel paths).
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi

# ─── ffmpeg ───────────────────────────────────────────────────────────────────
# parallax requires a drawtext-capable ffmpeg (built with libfreetype).
# On macOS, the standard Homebrew ffmpeg bottle omits freetype — ffmpeg-full
# includes it. On Linux, the distro ffmpeg package already includes freetype.
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "→ Installing ffmpeg..."
    if command -v brew >/dev/null 2>&1; then
        brew install ffmpeg-full
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y ffmpeg
    elif command -v yum >/dev/null 2>&1; then
        sudo yum install -y ffmpeg
    else
        echo ""
        echo "Error: ffmpeg not found and no known package manager available."
        echo "  macOS:  brew install ffmpeg-full"
        echo "  Linux:  sudo apt install ffmpeg"
        echo "  Other:  https://ffmpeg.org/download.html"
        exit 1
    fi
elif [ "$(uname)" = "Darwin" ]; then
    # Already have ffmpeg — check it supports drawtext (libfreetype).
    if ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q drawtext; then
        echo "→ Upgrading to ffmpeg-full (drawtext/libfreetype required)..."
        brew install ffmpeg-full
    fi
fi

# ─── parallax ─────────────────────────────────────────────────────────────────
echo "→ Installing parallax..."
uv tool install --python 3.11 git+https://github.com/ianjamesburke/parallax-v0

echo ""
echo "✓ parallax installed. Set your API key and go:"
echo ""
echo "  export OPENROUTER_API_KEY=sk-or-..."
echo "  parallax --help"
echo ""
echo "For tab completion:  parallax completions install"
echo ""
echo "Note: first run of 'parallax ingest' or 'parallax audio transcribe' will"
echo "download ~2GB of WhisperX model weights. Subsequent runs use the cache."
