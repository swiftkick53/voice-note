# Voice Notes

macOS menu bar app for voice-to-text capture with Claude AI processing.

## Features
- Global hotkey (F1) to record voice notes
- Local transcription via mlx-whisper (runs on Apple Silicon)
- Claude AI processing: clean format, action items, meeting summary, email draft
- Saves to Obsidian vault as markdown
- Dictation mode: types transcribed text at cursor position
- Soft Matter UI — warm dark palette, animated blob, bento library grid

## Requirements
- macOS (Apple Silicon)
- [uv](https://github.com/astral-sh/uv)
- Claude Code CLI (`claude`)
- Obsidian vault

## Setup
```bash
# Install dependencies and run
~/.local/bin/uv run voice_notes.py
```

Or open `Voice Notes.app` if you've built the app bundle.

## Config
Edit `config.json` to change the hotkey, vault path, sample rate, etc.
