# Voice Notes × Stream Deck

Two ways to drive Voice Notes from an Elgato Stream Deck.

## Option A — Hotkey keys (no plugins)

In the Stream Deck app, drag **System → Hotkey** onto a key and assign the
app's hotkeys (System screen shows/edits them; defaults **F1** dictation,
**F2** note). Use the icons in `icons/` for the key art.

Tip: rebind the app's hotkeys to combos no keyboard would send (e.g.
`⌘⇧F13`) and let the deck own them exclusively.

## Option B — Control API (per-function keys)

Voice Notes runs a loopback-only HTTP API (port `48752`, configurable via
`control_port` in config.json; `0` disables). Every request needs the token
from `control_token` in config.json — pass it as `?token=…` or an `X-Token`
header.

| Key | Request |
|---|---|
| Toggle dictation | `POST http://127.0.0.1:48752/dictate?token=…` |
| Toggle note      | `POST http://127.0.0.1:48752/note?token=…` |
| Cancel           | `POST http://127.0.0.1:48752/cancel?token=…` |
| Preview / Save   | `POST http://127.0.0.1:48752/save?token=…` |
| Set action       | `POST http://127.0.0.1:48752/action/clean_format?token=…` |
| State (for feedback) | `GET http://127.0.0.1:48752/status?token=…` |

Actions: `clean_format`, `action_items`, `meeting_summary`, `draft_email`,
`raw`. **Save** is contextual: on the note-review screen it opens the
Preview; on the Preview screen it saves to the vault.

Use any web-request Stream Deck plugin from the Elgato store ("API
Request", "Web Requests", etc.) to fire these. Test from Terminal:

```bash
curl -X POST "http://127.0.0.1:48752/status?token=$(python3 -c "import json;print(json.load(open('config.json'))['control_token'])")"
```

## Suggested layout (15-key deck, top-right block)

```
[ Dictate ]  [ Note ]     [ Cancel ]
[ ✦ Clean ]  [ ☰ Items ]  [ ✓ Save ]
```

## Icons

`icons/*.png` — 144×144 key art rendered from the app's actual blob:
`dictate` (cyan) / `dictate-amber`, `note` ✎, `recording` (red, for
multi-state keys), `cancel` ×, `save` ✓, plus per-action glyphs.
Regenerate at other theme hues with `_icon.html`:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --window-size=144,144 --screenshot=icons/custom.png "file://$PWD/_icon.html#hue=290&glyph=✦"
```
