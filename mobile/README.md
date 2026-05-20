# Voice Notes — Mobile

Expo / React Native app for iOS. Records voice, transcribes with Whisper, processes with Claude, saves to your Obsidian vault.

## Quick start

```bash
cd mobile
npm install
npx expo start
```

Scan the QR code with **Expo Go** on your iPhone.

## First-time setup

1. Open the **Settings** tab
2. Add your **OpenAI API key** (for Whisper transcription) — get one at platform.openai.com
3. Add your **Anthropic API key** (for Claude processing) — get one at console.anthropic.com
4. Set your **Obsidian Vault Name** (must match the folder name in iCloud Drive → Obsidian)

## How it works

1. Tap the record button on the **Capture** tab
2. Speak your note
3. Tap again to stop — it transcribes and processes automatically
4. The note saves to `iCloud Drive/Obsidian/<vault>/Voice Notes/` and appears in Obsidian on all your devices

## Notes saved as

```
YYYY-MM-DD HH-mm <title>.md
```
With YAML frontmatter (date, duration, tags: [voice-note]) matching the macOS app format.

## TODOs / next steps

- [ ] Add OpenAI API key (Settings screen)  
- [ ] Add Anthropic API key (Settings screen)
- [ ] Verify iCloud vault path matches your setup
- [ ] Blob animation on capture screen (port from overlay.html)
- [ ] Suggest title via Claude (like macOS app)
- [ ] Custom prompt support
- [ ] Waveform visualizer during recording
