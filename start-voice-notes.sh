#!/bin/bash
# Start the voice notes menu bar app.
# First run installs Python 3.11+ and all dependencies automatically via uv.
# Edit config.json to change the hotkey or other settings.
cd "$(dirname "$0")"
~/.local/bin/uv run voice_notes.py
