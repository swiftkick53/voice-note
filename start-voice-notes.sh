#!/bin/bash
# Start the voice notes menu bar app.
#
# Runs from a pinned venv at ~/.local/voice-notes-venv so the Python binary
# path never changes — macOS ties Microphone/Accessibility permissions to
# that exact binary, and a moving path (like uv's hashed cache envs) silently
# breaks them. Rebuild the venv with:
#   ~/.local/voice-notes-python/bin/python3.14 -m venv ~/.local/voice-notes-venv
#   ~/.local/voice-notes-venv/bin/pip install rumps sounddevice soundfile \
#     mlx-whisper pynput pyobjc-framework-Cocoa pyobjc-framework-WebKit \
#     pyobjc-framework-Speech pyobjc-framework-AVFoundation
cd "$(dirname "$0")"

PY="$HOME/.local/voice-notes-venv/bin/python3"
if [ ! -x "$PY" ]; then
  # Fallback to uv if the pinned venv is missing
  exec "$HOME/.local/bin/uv" run voice_notes.py
fi

# Refuse to launch broken code — a syntax error otherwise means the app
# silently never appears.
if ! "$PY" -m py_compile voice_notes.py 2>/tmp/voice-notes-syntax-error.log; then
  osascript -e 'display notification "Syntax error in voice_notes.py — see /tmp/voice-notes-syntax-error.log" with title "Voice Notes failed to start"'
  exit 1
fi

exec "$PY" voice_notes.py
