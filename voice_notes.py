#!/usr/bin/env python3
"""Voice-to-text menu bar app with GUI overlay, Claude integration, and dictation mode."""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rumps>=0.4.0",
#     "sounddevice>=0.5.0",
#     "soundfile>=0.13.0",
#     "mlx-whisper>=0.4.0",
#     "pynput>=1.7.0",
#     "pyobjc-framework-Cocoa>=10.0",
#     "pyobjc-framework-WebKit>=10.0",
# ]
# ///

import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path

__version__ = "0.3.0"

import rumps
import sounddevice as sd
import soundfile as sf

# PyObjC imports
import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSColor,
    NSEventTypeLeftMouseDown,
    NSFloatingWindowLevel,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSPanel,
    NSScreen,
    NSView,
    NSViewWidthSizable,
    NSViewHeightSizable,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
)
from AppKit import NSApplicationActivationPolicyRegular, NSApplicationActivationPolicyAccessory
from Foundation import NSObject, NSURL
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController


DEBUG_LOG = Path("/tmp/voice-notes-debug.log")

def debug(msg):
    """Write debug message to file (stdout unreliable in menu bar apps)."""
    with open(DEBUG_LOG, "a") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    print(msg)

def notify(title, subtitle, message):
    """Send a notification, silently ignoring failures."""
    try:
        rumps.notification(title, subtitle, message)
    except RuntimeError:
        debug(f"[{title}] {subtitle}: {message}")


# ---------------------------------------------------------------------------
# App State
# ---------------------------------------------------------------------------

class AppState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    POST_RECORDING = "post_recording"
    SAVING = "saving"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


CONFIG = load_config()
VAULT = Path(os.path.expanduser(CONFIG.get("vault_path", "~/Documents/Claude Brain Obsidian/Claude Brain")))
NOTES_DIR = VAULT / CONFIG.get("notes_folder", "08 Summaries/Voice Notes")
DAILY_DIR = VAULT / "02 Daily"
WHISPER_MODEL = CONFIG.get("whisper_model", "mlx-community/whisper-large-v3-turbo")
SAMPLE_RATE = CONFIG.get("sample_rate", 16000)
INPUT_CHANNEL = CONFIG.get("input_channel", 1)
NOTES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Action prompt templates
# ---------------------------------------------------------------------------

ACTION_PROMPTS = {
    "clean_format": """You are processing a voice note transcript. Clean it up and organize it for an Obsidian vault.

Rules:
1. Remove all filler words (um, uh, like, you know, sort of, kind of, basically, actually, right, I mean)
2. Fix grammar and punctuation while preserving the speaker's natural voice
3. Organize into logical sections with ## headers if the note covers multiple topics
4. Add a > [!tldr] callout at the top with a 1-2 sentence summary
5. Add [[wikilinks]] around notable concepts, people, tools, companies, and ideas
6. Do NOT add any frontmatter — that's handled separately
7. Return ONLY the cleaned markdown content, no code fences, no preamble

Transcript:
{transcript}""",

    "action_items": """Extract action items from this voice note transcript for an Obsidian vault.

Format:
1. Add a > [!tldr] callout with a summary of key decisions and context
2. Create a ## Action Items section with a markdown checklist (- [ ] items)
3. Tag people mentioned with [[wikilinks]]
4. Add a ## Context section with relevant background from the transcript
5. Do NOT add any frontmatter — that's handled separately
6. Return ONLY the cleaned markdown content, no code fences, no preamble

Transcript:
{transcript}""",

    "meeting_summary": """Summarize this meeting recording transcript for an Obsidian vault.

Format:
1. > [!tldr] callout with 1-2 sentence meeting summary
2. ## Attendees — list people mentioned (use [[wikilinks]])
3. ## Discussion — key topics discussed, organized by theme
4. ## Decisions — bullet list of decisions made
5. ## Action Items — checklist with owners in [[wikilinks]]
6. ## Follow-ups — things to revisit later
7. Do NOT add any frontmatter — that's handled separately
8. Return ONLY the cleaned markdown content, no code fences, no preamble

Transcript:
{transcript}""",

    "draft_email": """Convert this voice note into a polished email draft.

Rules:
1. Start with "**Subject:** " on the first line
2. Professional but natural tone matching the speaker's voice
3. Organize into clear paragraphs
4. If action items are mentioned, include them as a bulleted list
5. End with an appropriate closing
6. Do NOT add any frontmatter
7. Return ONLY the email content, no code fences, no preamble

Transcript:
{transcript}""",
}

TITLE_PROMPT = """Given this voice note transcript, suggest a short descriptive title (3-7 words, suitable as a filename). Return ONLY the title — no quotes, no punctuation, no explanation.

Transcript:
{transcript}"""


# ---------------------------------------------------------------------------
# Audio recording
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.audio_queue = queue.Queue()
        self.stream = None
        self.recording = False
        self.start_time = None
        self.last_level = 0.0  # 0..1, RMS-derived for visualization

    def _callback(self, indata, frames, time_info, status):
        ch = INPUT_CHANNEL - 1
        chunk = indata[:, ch:ch + 1].copy()
        self.audio_queue.put(chunk)
        # Compute simple RMS for level meter
        try:
            import numpy as np
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            # Boost: typical speech RMS at desk-distance is 0.02-0.10, so
            # multiply by ~10 to land comfortably in 0.2-1.0 for the overlay's
            # waveform. The JS side further shapes via sqrt().
            level = min(1.0, rms * 10.0)
            # Smooth toward new value (slightly faster attack than before).
            self.last_level = self.last_level * 0.35 + level * 0.65
        except Exception:
            pass

    def start(self):
        self.audio_queue = queue.Queue()
        self.recording = True
        self.start_time = time.time()
        self.last_level = 0.0
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=INPUT_CHANNEL,
            dtype="float32",
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        self.recording = False
        self.last_level = 0.0
        duration = time.time() - self.start_time if self.start_time else 0
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        frames = []
        while not self.audio_queue.empty():
            frames.append(self.audio_queue.get())

        if not frames:
            return None, 0

        import numpy as np
        audio_data = np.concatenate(frames, axis=0)

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, prefix="voice_note_")
        sf.write(tmp.name, audio_data, self.sample_rate)
        return tmp.name, int(duration)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

_whisper_loaded = False


def transcribe(wav_path):
    global _whisper_loaded
    import mlx_whisper

    if not _whisper_loaded:
        notify("Voice Notes", "Loading Whisper model...", "First transcription may take a moment.")
        _whisper_loaded = True

    import numpy as np
    data, sr = sf.read(wav_path, dtype="float32")
    if sr != 16000:
        from fractions import Fraction
        ratio = Fraction(16000, sr)
        n_samples = int(len(data) * ratio)
        indices = np.arange(n_samples) * sr / 16000
        indices = np.clip(indices.astype(int), 0, len(data) - 1)
        data = data[indices]
    if data.ndim > 1:
        data = data.mean(axis=1)

    result = mlx_whisper.transcribe(data, path_or_hf_repo=WHISPER_MODEL, language="en")
    return result.get("text", "").strip()


# ---------------------------------------------------------------------------
# Claude processing
# ---------------------------------------------------------------------------

def process_with_claude(transcript, action="clean_format", project=None, custom_prompt=None):
    if action == "raw":
        return f"> [!tldr]\n> Voice note transcript\n\n{transcript}"

    if action == "custom" and custom_prompt:
        prompt = f"{custom_prompt}\n\nTranscript:\n{transcript}"
    else:
        template = ACTION_PROMPTS.get(action, ACTION_PROMPTS["clean_format"])
        prompt = template.format(transcript=transcript)

    # Resolve claude CLI to an absolute path so launchd-spawned contexts
    # (where PATH may be minimal) still find it.
    claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    cmd = [claude_bin, "-p", prompt]
    if project:
        cmd.extend(["--project", project])

    def _run(cmd):
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(VAULT),
        )

    try:
        result = _run(cmd)
        # If we get a 401, the OAuth token has expired. Run a no-op prompt to
        # force a token refresh, then retry once.
        if result.returncode != 0 and "401" in (result.stdout or ""):
            debug("[voice-notes] Claude 401 — refreshing session and retrying")
            try:
                subprocess.run(
                    [claude_bin, "-p", "hi"],
                    capture_output=True, text=True, timeout=60, cwd=str(VAULT),
                )
            except Exception:
                pass
            result = _run(cmd)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        debug(
            f"[voice-notes] Claude call failed: rc={result.returncode}, "
            f"stdout={(result.stdout or '')[:500]!r}, "
            f"stderr={(result.stderr or '')[:500]!r}"
        )
    except subprocess.TimeoutExpired:
        debug("[voice-notes] Claude call timed out after 300s")
    except FileNotFoundError:
        debug(f"[voice-notes] Claude CLI not found at {claude_bin}")
    except Exception as exc:
        debug(f"[voice-notes] Claude call error: {exc!r}")

    return f"> [!tldr]\n> Voice note transcript\n\n{transcript}"


def suggest_title(transcript):
    prompt = TITLE_PROMPT.format(transcript=transcript[:1000])
    claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt],
            capture_output=True, text=True, timeout=60, cwd=str(VAULT),
        )
        if result.returncode == 0 and result.stdout.strip():
            title = result.stdout.strip().strip('"').strip("'").strip()
            title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
            return title[:60]
        debug(
            f"[voice-notes] Title suggest failed: rc={result.returncode}, "
            f"stderr={(result.stderr or '')[:300]!r}"
        )
    except subprocess.TimeoutExpired:
        debug("[voice-notes] Title suggest timed out")
    except FileNotFoundError:
        debug(f"[voice-notes] Claude CLI not found at {claude_bin}")
    except Exception as exc:
        debug(f"[voice-notes] Title suggest error: {exc!r}")
    return None


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def copy_to_clipboard(text):
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Audio playback
# ---------------------------------------------------------------------------

class AudioPlayer:
    def __init__(self):
        self.proc = None

    def play(self, wav_path, on_finish=None):
        self.stop()
        if not wav_path or not os.path.exists(wav_path):
            return False
        try:
            self.proc = subprocess.Popen(
                ["afplay", wav_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return False

        if on_finish:
            def watch():
                self.proc.wait()
                on_finish()
            threading.Thread(target=watch, daemon=True).start()
        return True

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None

    def is_playing(self):
        return self.proc is not None and self.proc.poll() is None


# ---------------------------------------------------------------------------
# Recent notes listing
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

def list_recent_notes(limit=20):
    """Return a list of recent voice notes from NOTES_DIR."""
    notes = []
    if not NOTES_DIR.exists():
        return notes
    files = sorted(
        [p for p in NOTES_DIR.glob("*.md") if p.is_file()],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:limit]
    now = datetime.now()
    for path in files:
        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime)
            duration = 0
            try:
                head = path.read_text()[:600]
                m = FRONTMATTER_RE.match(head)
                if m:
                    fm = m.group(1)
                    for line in fm.split("\n"):
                        if line.startswith("duration:"):
                            try:
                                duration = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                pass
            except Exception:
                pass

            # Friendly modified label
            delta = now - mtime
            if delta.days >= 7:
                label = mtime.strftime("%b %d")
            elif delta.days >= 1:
                label = f"{delta.days}d ago"
            elif delta.seconds >= 3600:
                label = f"{delta.seconds // 3600}h ago"
            elif delta.seconds >= 60:
                label = f"{delta.seconds // 60}m ago"
            else:
                label = "just now"

            notes.append({
                "filename": path.name,
                "title": path.stem,
                "modified": label,
                "duration": duration,
            })
        except OSError:
            continue
    return notes


def open_note_in_obsidian(filename):
    """Open a note in Obsidian (or default editor)."""
    path = NOTES_DIR / filename
    if not path.exists():
        return False
    # Try Obsidian URI scheme first
    vault_name = VAULT.name
    note_rel = path.relative_to(VAULT).as_posix()
    try:
        from urllib.parse import quote
        uri = f"obsidian://open?vault={quote(vault_name)}&file={quote(note_rel)}"
        subprocess.run(["open", uri], check=False)
        return True
    except Exception:
        try:
            subprocess.run(["open", str(path)], check=False)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Save to vault
# ---------------------------------------------------------------------------

def save_note(content, duration, title=None):
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M")
    iso_str = now.strftime("%Y-%m-%dT%H:%M")

    if title:
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        if safe_title:
            filename = f"{date_str} {time_str} {safe_title}.md"
        else:
            filename = f"{date_str} {time_str} Voice Note.md"
    else:
        filename = f"{date_str} {time_str} Voice Note.md"

    frontmatter = f"""---
created: {iso_str}
updated: {iso_str}
type: voice-note
duration: {duration}
tags:
  - voice-note
unread: true
---

"""
    note_path = NOTES_DIR / filename
    with open(note_path, "w") as f:
        f.write(frontmatter + content)

    # Update daily note
    day_name = now.strftime("%a")
    daily_dir = DAILY_DIR / now.strftime("%Y") / now.strftime("%m")
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_filename = now.strftime(f"%m-%d-%y {day_name}.md")
    daily_path = daily_dir / daily_filename

    note_title = filename.replace(".md", "")
    tldr_line = ""
    for line in content.split("\n"):
        if line.startswith(">") and "tldr" not in line.lower():
            tldr_line = line.lstrip("> ").strip()
            break

    entry = f"- voice note [[{note_title}]]"
    if tldr_line:
        entry += f" — {tldr_line[:100]}"

    if daily_path.exists():
        existing = daily_path.read_text()
        if note_title not in existing:
            with open(daily_path, "a") as f:
                f.write(f"\n{entry}")
    else:
        with open(daily_path, "w") as f:
            f.write(f"""---
created: {iso_str}
updated: {iso_str}
unread: true
---

## content summary
{entry}
""")

    return note_title


# ---------------------------------------------------------------------------
# Hotkey listener
# ---------------------------------------------------------------------------

def parse_hotkey(hotkey_str):
    from pynput import keyboard

    parts = [p.strip().lower() for p in hotkey_str.split("+")]
    modifiers = set()
    key = None

    modifier_map = {
        "cmd": keyboard.Key.cmd, "command": keyboard.Key.cmd,
        "ctrl": keyboard.Key.ctrl, "control": keyboard.Key.ctrl,
        "alt": keyboard.Key.alt, "option": keyboard.Key.alt,
        "shift": keyboard.Key.shift,
    }

    for part in parts:
        if part in modifier_map:
            modifiers.add(modifier_map[part])
        else:
            if len(part) == 1:
                key = keyboard.KeyCode.from_char(part)
            else:
                key = getattr(keyboard.Key, part, None)

    return modifiers, key


def start_hotkey_listener(toggle_callback):
    hotkey_str = CONFIG.get("hotkey", "cmd+shift+r")
    modifiers, key = parse_hotkey(hotkey_str)

    if not key:
        print(f"Warning: Could not parse hotkey '{hotkey_str}', hotkey disabled.")
        return

    from pynput import keyboard

    current_modifiers = set()

    def on_press(k):
        if k in modifiers:
            current_modifiers.add(k)
        if current_modifiers == modifiers and k == key:
            toggle_callback()

    def on_release(k):
        current_modifiers.discard(k)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()


# ---------------------------------------------------------------------------
# WKWebView message handler
# ---------------------------------------------------------------------------

class ClickablePanel(NSPanel):
    """NSPanel subclass that accepts key status + handles window dragging.

    WKWebView swallows every mouse event it receives, so the usual
    `setMovableByWindowBackground:` and the `mouseDownCanMoveWindow`
    sibling-view trick both lose to the WebView's internal hit testing.
    Instead we intercept `sendEvent:` at the window level: if the user
    left-clicks inside the HTML header's dead zone (center strip between
    the app-title pill and the min/close buttons), we hand the event to
    `performWindowDragWithEvent:` — the documented macOS API that runs a
    full drag loop including snap-to-edge and multi-monitor handling.
    Clicks outside that zone fall through to normal event dispatch."""

    # Header-drag geometry. Coordinates are in window/content space (origin
    # bottom-left on macOS). The Soft Matter header has a dot+title on the
    # left, then a mode-toggle and win-controls on the right. We make only
    # the title/subtitle strip draggable (roughly x 60–200) so the dot,
    # mode buttons, and window controls keep receiving clicks.
    HEADER_DRAG_HEIGHT = 52
    HEADER_LEFT_INSET  = 60
    HEADER_RIGHT_INSET = 200

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return False

    def sendEvent_(self, event):
        if event.type() == NSEventTypeLeftMouseDown:
            try:
                point = event.locationInWindow()   # window coords, bottom-left origin
                frame_size = self.frame().size
                in_header = point.y >= (frame_size.height - self.HEADER_DRAG_HEIGHT)
                in_mid = (
                    self.HEADER_LEFT_INSET < point.x <
                    (frame_size.width - self.HEADER_RIGHT_INSET)
                )
                if in_header and in_mid:
                    # performWindowDragWithEvent: runs the full drag loop;
                    # it returns immediately after starting.
                    self.performWindowDragWithEvent_(event)
                    return
            except Exception as exc:
                debug(f"[voice-notes] drag intercept error: {exc}")
        objc.super(ClickablePanel, self).sendEvent_(event)


class NavDelegate(NSObject):
    """WKNavigationDelegate to log webview load events."""
    def webView_didFinishNavigation_(self, webview, navigation):
        debug("WebView finished loading!")
        webview.evaluateJavaScript_completionHandler_(
            "document.querySelector('.record-btn') ? 'record-btn-found' : 'record-btn-NOT-found'",
            lambda result, error: debug(f"JS test: result={result}, error={error}"),
        )
        webview.evaluateJavaScript_completionHandler_(
            "typeof window.webkit !== 'undefined' && typeof window.webkit.messageHandlers !== 'undefined' && typeof window.webkit.messageHandlers.bridge !== 'undefined' ? 'bridge-ok' : 'bridge-MISSING'",
            lambda result, error: debug(f"Bridge test: result={result}, error={error}"),
        )

    def webView_didFailNavigation_withError_(self, webview, nav, error):
        debug(f"WebView FAILED navigation: {error}")

    def webView_didFailProvisionalNavigation_withError_(self, webview, nav, error):
        debug(f"WebView FAILED provisional: {error}")


WKScriptMessageHandlerProtocol = objc.protocolNamed('WKScriptMessageHandler')


class WebViewMessageHandler(NSObject, protocols=[WKScriptMessageHandlerProtocol]):
    """Handles messages from the WKWebView JavaScript bridge."""

    def init(self):
        self = objc.super(WebViewMessageHandler, self).init()
        if self is None:
            return None
        self.app = None
        self.panel = None  # OverlayPanel — set after panel is built
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        """Unused fallback — the overlay currently routes all JS→Python calls
        through the document.title KVO bridge (see TitleObserver). This handler
        stays registered so window.webkit.messageHandlers.bridge exists, which
        some JS guards check before falling back to the title bridge.
        """
        body = message.body()
        if not isinstance(body, dict):
            return
        if self.panel is not None:
            self.panel._on_bridge_message(body)


# ---------------------------------------------------------------------------
# Overlay Panel (WKWebView-based)
# ---------------------------------------------------------------------------

class TitleObserver(NSObject):
    """KVO observer that watches WKWebView title changes as a JS→Python bridge."""

    def init(self):
        self = objc.super(TitleObserver, self).init()
        if self is None:
            return None
        self.panel = None  # set to OverlayPanel instance
        return self

    def observeValueForKeyPath_ofObject_change_context_(self, keyPath, obj, change, ctx):
        if keyPath != "title" or self.panel is None:
            return
        title = obj.title()
        if not title or not title.startswith("BRIDGE:"):
            return
        try:
            payload = title[7:]
            body = json.loads(payload)
            seq = body.get("_seq", 0)
            if seq <= self.panel._last_msg_seq:
                return  # duplicate
            self.panel._last_msg_seq = seq
            debug(f"Bridge message: {body}")
            self.panel._on_bridge_message(body)
        except Exception as e:
            debug(f"Bridge parse error: {e}")


class OverlayPanel:
    """Floating macOS overlay panel with WKWebView for rendering."""

    WIDTH = 420
    HEIGHT = 540

    def __init__(self, app):
        self.app = app
        self.webview = None
        self.mode = CONFIG.get("default_mode", "note")
        self.transcript = None
        self.suggested_title_text = None
        self.wav_path = None
        self.duration = 0
        self._last_msg_seq = 0

        # KVO observer for title changes (our JS→Python bridge)
        self.title_observer = TitleObserver.alloc().init()
        self.title_observer.panel = self

        self._create_panel()

    def _create_panel(self):
        """Create a floating window with WKWebView."""
        rect = NSMakeRect(0, 0, self.WIDTH, self.HEIGHT)
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskFullSizeContentView
            | NSWindowStyleMaskResizable
        )
        self.panel = ClickablePanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False,
        )
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setTitlebarAppearsTransparent_(True)
        self.panel.setTitleVisibility_(NSWindowTitleHidden)
        self.panel.setFloatingPanel_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setTitle_("Voice Notes")
        # Make the panel freely draggable. `setMovable_` is default-YES for
        # NSPanel, but spell it out; `setMovableByWindowBackground_` covers
        # non-WebView regions (e.g. resize overscroll). The real drag affordance
        # is the DragHandleView installed over the WebView header below.
        self.panel.setMovable_(True)
        self.panel.setMovableByWindowBackground_(True)
        # Let macOS pick the window bg per system appearance (light/dark).
        # The HTML body paints the real surface via var(--bg); this only shows
        # during the first paint and behind any resize overscroll.
        self.panel.setBackgroundColor_(NSColor.windowBackgroundColor())
        self.panel.setReleasedWhenClosed_(False)
        # Allow the user to resize the overlay; clamp to a usable minimum.
        self.panel.setMinSize_((360, 420))

        # Hide native traffic light buttons
        self.panel.standardWindowButton_(0).setHidden_(True)
        self.panel.standardWindowButton_(1).setHidden_(True)
        self.panel.standardWindowButton_(2).setHidden_(True)

        # Position near top-center of screen
        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - self.WIDTH) / 2
        y = screen.size.height - self.HEIGHT - 60
        self.panel.setFrameOrigin_((x, y))

        # Set up WKWebView with message handler
        self.msg_handler = WebViewMessageHandler.alloc().init()
        self.msg_handler.app = self.app
        self.msg_handler.panel = self

        config = WKWebViewConfiguration.alloc().init()
        user_content = config.userContentController()
        user_content.addScriptMessageHandler_name_(self.msg_handler, "bridge")

        prefs = config.preferences()
        prefs.setValue_forKey_(True, "developerExtrasEnabled")

        content_view = self.panel.contentView()
        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            content_view.bounds(), config,
        )
        self.webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        # Avoid a white flash during load in dark mode; HTML body paints its own bg.
        try:
            self.webview.setValue_forKey_(False, "drawsBackground")
        except Exception:
            pass

        # Navigation delegate for load diagnostics
        self.nav_delegate = NavDelegate.alloc().init()
        self.webview.setNavigationDelegate_(self.nav_delegate)

        content_view.addSubview_(self.webview)

        # Window dragging is handled by ClickablePanel.sendEvent_ — it
        # intercepts left-mouse-downs in the header's dead-zone center
        # strip and calls performWindowDragWithEvent:, which runs the full
        # native drag loop even though WKWebView would otherwise eat the
        # event. No subview is needed.

        # Register KVO observer on webview title for JS→Python bridge
        self.webview.addObserver_forKeyPath_options_context_(
            self.title_observer, "title", 1, None,  # 1 = NSKeyValueObservingOptionNew
        )

        # Load HTML as string
        html_path = SCRIPT_DIR / "overlay.html"
        html_content = html_path.read_text()
        base_url = NSURL.fileURLWithPath_(str(SCRIPT_DIR) + "/")
        self.webview.loadHTMLString_baseURL_(html_content, base_url)
        debug(f"Loading overlay HTML ({len(html_content)} bytes)")

    def _on_bridge_message(self, body):
        """Handle a message from the JS bridge (via title KVO)."""
        action = body.get("action", "")
        debug(f"Handling action: {action}")

        if action == "debug":
            debug(f"[JS DEBUG] {body}")
            return
        if action == "record":
            self.app.toggle_from_overlay()
        elif action == "save":
            self.app.save_from_overlay(body)
        elif action == "discard":
            self.app.discard_from_overlay()
        elif action == "mode":
            mode = body.get("mode", "note")
            self.app.set_mode(mode)
        elif action == "copy_again":
            self.app.copy_again()
        elif action == "done":
            self.app.done_from_overlay()
        elif action == "close":
            self.app.hide_overlay()
        elif action == "minimize":
            self.app.hide_overlay()
        elif action == "playback":
            self.app.playback_from_overlay()
        elif action == "stop_playback":
            self.app.stop_playback_from_overlay()
        elif action == "get_recent_notes":
            self.app.send_recent_notes()
        elif action == "refresh_notes":
            self.app.send_recent_notes()
        elif action == "open_note":
            filename = body.get("filename", "")
            if filename:
                open_note_in_obsidian(filename)
        elif action == "get_settings":
            self.app.send_settings()
        elif action == "save_settings":
            self.app.save_settings_from_overlay(body)
        elif action == "copy_error":
            text = body.get("text", "")
            if text:
                copy_to_clipboard(text)
                notify("Voice Notes", "Copied", "Error details copied.")
        elif action == "retry":
            self.app.retry_from_overlay()

    def _eval_js(self, script):
        """Evaluate JavaScript in the webview."""
        if self.webview:
            self.webview.evaluateJavaScript_completionHandler_(script, None)

    def _js_escape(self, text):
        """Escape a string for safe embedding in JavaScript."""
        if not text:
            return ""
        return (
            text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("</", "<\\/")
        )

    # --- State transitions ---

    def set_state(self, state):
        """Transition the overlay UI to a new state."""
        self._eval_js(f"setState('{state}')")

    def show(self):
        """Show the overlay and set to idle state."""
        debug("show() called")
        # Switch to Regular policy so the window can become key and receive clicks
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        self.panel.makeKeyAndOrderFront_(None)
        self.panel.makeKeyWindow()
        debug(f"Panel visible={self.panel.isVisible()}, key={self.panel.isKeyWindow()}")
        # Apply config defaults after a short delay for webview to be ready
        self._apply_defaults()

    def _apply_defaults(self):
        """Send config defaults to the webview."""
        hotkey = self._js_escape(CONFIG.get("hotkey", "cmd+shift+r"))
        self._eval_js(f"setHotkey('{hotkey}')")
        self._eval_js(f"setModeFromPython('{self.mode}')")

        default_action = CONFIG.get("default_action", "clean_format")
        self._eval_js(f"setDefaultAction('{default_action}')")

        clipboard = "true" if CONFIG.get("copy_to_clipboard", False) else "false"
        self._eval_js(f"setClipboardDefault({clipboard})")

        projects = CONFIG.get("projects", [{"name": "Default", "flag": None}])
        projects_json = json.dumps(projects)
        self._eval_js(f"setProjects({projects_json})")

        # Real telemetry
        self._eval_js(f"setSampleRate({SAMPLE_RATE})")
        model_escaped = self._js_escape(WHISPER_MODEL)
        self._eval_js(f"setEngineModel('{model_escaped}')")
        self._eval_js(f"setVersion('{__version__}')")

    def push_audio_level(self, level):
        """Push a 0..1 audio level to the JS waveform."""
        self._eval_js(f"setAudioLevel({level:.4f})")

    def push_recent_notes(self, notes):
        notes_json = json.dumps(notes)
        self._eval_js(f"setRecentNotes({notes_json})")

    def push_settings(self, settings):
        settings_json = json.dumps(settings)
        self._eval_js(f"setSettings({settings_json})")

    def push_post_note_data(self, transcript, duration):
        data = json.dumps({"transcript": transcript, "duration": duration})
        self._eval_js(f"setPostNoteData({data})")

    def push_playing_state(self, playing):
        self._eval_js(f"setPlayingState({'true' if playing else 'false'})")

    def push_error(self, title, message):
        title_e = self._js_escape(title)
        msg_e = self._js_escape(message)
        self._eval_js(f"showError('{title_e}', '{msg_e}')")

    def hide(self):
        self.panel.orderOut_(None)
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def is_visible(self):
        return self.panel.isVisible()

    def set_idle(self):
        self.set_state("idle")

    def set_recording(self):
        self.set_state("recording")

    def set_transcribing(self):
        self.set_state("transcribing")

    def set_post_note(self, transcript=None, duration=None):
        self.set_state("post_note")
        self._apply_defaults()
        if transcript is not None:
            self.push_post_note_data(transcript, duration or 0)

    def set_post_dictation(self, transcript):
        preview = self._js_escape(transcript[:500])
        self._eval_js(f"setTranscriptPreview('{preview}')")
        self.set_state("post_dictation")

    def set_saving(self):
        self.set_state("saving")

    def update_title_suggestion(self, title):
        self.suggested_title_text = title
        escaped = self._js_escape(title)
        self._eval_js(f"updateTitleSuggestion('{escaped}')")


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------

def _render_sf_symbol_png(symbol_name, out_path, point_size=18):
    """Render an SF Symbol to a PNG for use as a rumps icon.

    SF Symbols render as black-on-transparent when drawn with default
    attributes. rumps serves the PNG with template=True, so macOS auto-tints
    to black on light menu bars / white on dark — matching Apple's own menu
    extras. No manual tinting needed (previously we used sourceIn compositing
    which flooded the whole canvas and produced a solid square)."""
    try:
        from AppKit import (
            NSImage as _NSImage, NSBitmapImageRep, NSBitmapImageFileTypePNG,
            NSImageSymbolConfiguration,
        )
        glyph = _NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol_name, "Voice Notes",
        )
        if glyph is None:
            return False
        cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            point_size, 3,   # 3 = regular weight
        )
        glyph = glyph.imageWithSymbolConfiguration_(cfg)
        tiff = glyph.TIFFRepresentation()
        if tiff is None:
            return False
        rep = NSBitmapImageRep.imageRepWithData_(tiff)
        png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        return bool(png.writeToFile_atomically_(out_path, True))
    except Exception as exc:
        debug(f"[voice-notes] SF Symbol render failed for {symbol_name!r}: {exc}")
        return False


# Menu-bar icons per state. Rendered once on first use; rumps serves them as
# template PNGs so macOS handles tinting across appearances.
_ICON_CACHE_DIR = Path("/tmp/voice-notes-icons")
_ICON_CACHE_DIR.mkdir(exist_ok=True)

ICON_IDLE       = "waveform"
ICON_RECORDING  = "waveform.badge.mic"
ICON_PROCESSING = "waveform.badge.magnifyingglass"
ICON_FALLBACK_EMOJI = "\U0001F3A4"             # 🎤 — fallback only

def _icon_path(symbol_name):
    """Ensure a PNG exists for this symbol and return its path, or None."""
    out = _ICON_CACHE_DIR / f"{symbol_name.replace('.', '_')}.png"
    if not out.exists():
        if not _render_sf_symbol_png(symbol_name, str(out)):
            return None
    return str(out)


class VoiceNotesApp(rumps.App):
    def __init__(self):
        idle_icon = _icon_path(ICON_IDLE)
        # Keep rumps' default Quit menu item (⌘Q) — suppressing it before
        # left the app with no user-facing way to exit.
        quit_item = rumps.MenuItem("Quit Voice Notes", key="q")
        if idle_icon:
            super().__init__("Voice Notes", icon=idle_icon, template=True,
                             quit_button=quit_item)
        else:
            super().__init__("Voice Notes", title=ICON_FALLBACK_EMOJI,
                             quit_button=quit_item)
        self.recorder = Recorder()
        self.player = AudioPlayer()
        self.state = AppState.IDLE
        self._ui_queue = queue.Queue()

        self.menu = [
            rumps.MenuItem("Show Voice Notes", callback=self.toggle_overlay, key="s"),
            None,
            rumps.MenuItem("Open Notes Folder", callback=self.open_notes),
            None,
            # Quit is appended automatically by rumps from quit_button above.
        ]

        self.overlay = None
        self._previous_app = None  # frontmost app captured just before F1 shows overlay

        # State for current recording
        self._transcript = None
        self._wav_path = None
        self._duration = 0
        self._last_save_body = None  # for retry

        # Start hotkey listener
        start_hotkey_listener(self._hotkey_toggle)

        # Poll for UI updates from background threads
        self._poll_timer = rumps.Timer(self._poll_ui_queue, 0.2)
        self._poll_timer.start()

        # Push audio level to overlay during recording (~12 fps)
        self._level_timer = rumps.Timer(self._push_level, 0.08)
        self._level_timer.start()

    def _ensure_overlay(self):
        if self.overlay is None:
            self.overlay = OverlayPanel(self)
        return self.overlay

    # --- Menu-bar icon control ---

    def _set_icon(self, symbol_name):
        """Swap the menu-bar icon to the given SF Symbol. Uses rumps' own
        icon/template API which safely wraps the underlying NSStatusItem."""
        path = _icon_path(symbol_name)
        if path:
            try:
                self.icon = path
                self.template = True
                self.title = ""
                return
            except Exception as exc:
                debug(f"[voice-notes] Could not install SF Symbol: {exc}")
        # Fallback — emoji title when SF Symbols aren't available.
        self.icon = None
        self.title = {
            ICON_IDLE:       ICON_FALLBACK_EMOJI,
            ICON_RECORDING:  "\U0001F534",   # 🔴
            ICON_PROCESSING: "\u23F3",       # ⏳
        }.get(symbol_name, ICON_FALLBACK_EMOJI)

    def _schedule_ui(self, fn):
        self._ui_queue.put(fn)

    def _poll_ui_queue(self, timer):
        while not self._ui_queue.empty():
            try:
                fn = self._ui_queue.get_nowait()
                fn()
            except queue.Empty:
                break

    # --- Overlay visibility ---

    def toggle_overlay(self, _=None):
        debug("toggle_overlay called")
        overlay = self._ensure_overlay()
        debug(f"overlay created, visible={overlay.is_visible()}")
        if overlay.is_visible():
            overlay.hide()
        else:
            overlay.show()

    def hide_overlay(self):
        overlay = self._ensure_overlay()
        overlay.hide()

    # --- Hotkey ---

    def _hotkey_toggle(self):
        self._schedule_ui(self._handle_hotkey)

    def _handle_hotkey(self):
        debug(f"[voice-notes] Hotkey pressed, state={self.state.value}")
        overlay = self._ensure_overlay()

        if self.state == AppState.IDLE:
            # Capture frontmost app BEFORE the overlay steals focus,
            # so dictation mode can restore focus and type there.
            from AppKit import NSWorkspace
            self._previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            debug(f"[voice-notes] Previous app: {self._previous_app.bundleIdentifier() if self._previous_app else 'none'}")
            if not overlay.is_visible():
                overlay.show()
            self.start_recording()
        elif self.state == AppState.RECORDING:
            self.stop_recording()
        else:
            # Transcribing / post_recording / post_dictation / saving:
            # don't start a new recording (that would clobber the pending
            # transcript), but do re-surface the overlay so the user can
            # finish the in-flight note instead of being stuck.
            debug(f"[voice-notes] Hotkey in {self.state.value}: re-showing overlay")
            if not overlay.is_visible():
                overlay.show()
            else:
                NSApp.activateIgnoringOtherApps_(True)
                overlay.panel.makeKeyAndOrderFront_(None)

    # --- Recording control (called from overlay JS messages) ---

    def toggle_from_overlay(self):
        debug(f"[voice-notes] Record toggle, state={self.state.value}")
        if self.state == AppState.IDLE:
            self.start_recording()
        elif self.state == AppState.RECORDING:
            self.stop_recording()
        else:
            debug(f"[voice-notes] Toggle ignored in state {self.state.value}")

    def set_mode(self, mode):
        overlay = self._ensure_overlay()
        overlay.mode = mode
        CONFIG["default_mode"] = mode
        save_config(CONFIG)
        debug(f"[voice-notes] Mode set to {mode}")

    def start_recording(self):
        self.state = AppState.RECORDING
        self.recorder.start()
        self._set_icon(ICON_RECORDING)

        overlay = self._ensure_overlay()
        overlay.set_recording()
        if not overlay.is_visible():
            NSApp.activateIgnoringOtherApps_(True)
            overlay.panel.makeKeyAndOrderFront_(None)

        debug("[voice-notes] Recording started")

    def stop_recording(self):
        wav_path, duration = self.recorder.stop()
        self._set_icon(ICON_PROCESSING)
        self.state = AppState.TRANSCRIBING

        overlay = self._ensure_overlay()

        if not wav_path or duration < 1:
            self.state = AppState.IDLE
            self._set_icon(ICON_IDLE)
            overlay.set_idle()
            notify("Voice Notes", "Too short", "Recording was too short to process.")
            return

        self._wav_path = wav_path
        self._duration = duration
        overlay.wav_path = wav_path
        overlay.duration = duration
        overlay.set_transcribing()

        debug(f"[voice-notes] Stopped, transcribing {wav_path} ({duration}s)...")

        def do_transcribe():
            try:
                transcript = transcribe(wav_path)
                debug(f"[voice-notes] Transcript: {transcript[:200] if transcript else '(empty)'}")

                if not transcript:
                    self._schedule_ui(self._transcription_failed)
                    return

                self._transcript = transcript
                overlay.transcript = transcript

                if overlay.mode == "dictation":
                    copy_to_clipboard(transcript)
                    self._schedule_ui(lambda: self._show_dictation_result(transcript))
                else:
                    self._schedule_ui(self._show_post_recording)
                    threading.Thread(
                        target=self._suggest_title, args=(transcript,), daemon=True,
                    ).start()

            except Exception as e:
                import traceback
                traceback.print_exc()
                self._schedule_ui(lambda: self._transcription_error(str(e)))

        threading.Thread(target=do_transcribe, daemon=True).start()

    def _transcription_failed(self):
        self.state = AppState.IDLE
        self._set_icon(ICON_IDLE)
        self._ensure_overlay().set_idle()
        notify("Voice Notes", "No speech detected", "Try again with clearer audio.")

    def _transcription_error(self, msg):
        self.state = AppState.POST_RECORDING
        self._set_icon(ICON_IDLE)
        self._ensure_overlay().push_error("Transcription failed", msg)

    def _show_post_recording(self):
        self.state = AppState.POST_RECORDING
        self._set_icon(ICON_IDLE)
        self._ensure_overlay().set_post_note(
            transcript=self._transcript, duration=self._duration,
        )
        debug("[voice-notes] Showing post-recording UI")

    def _show_dictation_result(self, transcript):
        """In dictation mode: hide overlay, restore focus to previous app, type transcript."""
        self.state = AppState.IDLE
        self._set_icon(ICON_IDLE)
        overlay = self._ensure_overlay()
        overlay.hide()
        debug("[voice-notes] Dictation: typing at cursor")
        prev = self._previous_app
        self._previous_app = None
        threading.Thread(
            target=lambda: self._type_at_cursor(transcript, prev),
            daemon=True,
        ).start()

    def _type_at_cursor(self, text, prev_app):
        """Restore focus to prev_app and type text at the cursor position."""
        try:
            if prev_app:
                from AppKit import NSApplicationActivateIgnoringOtherApps
                prev_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                time.sleep(0.25)  # wait for focus to transfer
            from pynput.keyboard import Controller
            Controller().type(text)
            debug(f"[voice-notes] Typed {len(text)} chars at cursor")
        except Exception as exc:
            debug(f"[voice-notes] _type_at_cursor error: {exc!r}")

    def _suggest_title(self, transcript):
        title = suggest_title(transcript)
        if title:
            debug(f"[voice-notes] Title suggestion: {title}")
            self._schedule_ui(lambda: self._ensure_overlay().update_title_suggestion(title))

    # --- Save / discard (called from overlay JS messages) ---

    def save_from_overlay(self, body):
        """Handle save message from the webview.

        The edited transcript is NOT inlined in the bridge payload — it can be
        arbitrarily long and would overflow the title-based transport. Instead
        we pull it directly out of the textarea with evaluateJavaScript, then
        proceed with the save on a worker thread.
        """
        self._last_save_body = body
        overlay = self._ensure_overlay()

        def after_fetch(result, error):
            edited = result if isinstance(result, str) else None
            if error:
                debug(f"[voice-notes] Transcript fetch error: {error}")
            self._run_save(body, edited)

        overlay.webview.evaluateJavaScript_completionHandler_(
            "(function(){var t=document.getElementById('transcript-edit');"
            "return t ? t.value : '';})()",
            after_fetch,
        )

    def _run_save(self, body, edited_transcript):
        title = body.get("title", "").strip() or None
        if not title:
            title = self._ensure_overlay().suggested_title_text
        action_type = body.get("actionType", "clean_format")
        project = body.get("project", "") or None
        custom_prompt = body.get("customPrompt", "") or None
        copy_clip = body.get("copyClipboard", False)

        if edited_transcript and edited_transcript.strip():
            self._transcript = edited_transcript.strip()

        # Persist clipboard preference
        CONFIG["copy_to_clipboard"] = copy_clip
        save_config(CONFIG)

        self.state = AppState.SAVING
        self._set_icon(ICON_PROCESSING)
        self._ensure_overlay().set_saving()

        transcript = self._transcript
        duration = self._duration
        wav_path = self._wav_path

        debug(
            f"[voice-notes] Saving: action={action_type}, title={title}, "
            f"tlen={len(transcript or '')}"
        )

        def process():
            try:
                if copy_clip and transcript:
                    copy_to_clipboard(transcript)

                content = process_with_claude(
                    transcript, action=action_type, project=project,
                    custom_prompt=custom_prompt,
                )
                debug(f"[voice-notes] Claude output: {content[:200]}")

                note_title = save_note(content, duration, title=title)
                debug(f"[voice-notes] Saved: {note_title}")

                if wav_path:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass

                def on_success():
                    self.state = AppState.IDLE
                    self._set_icon(ICON_IDLE)
                    self._transcript = None
                    self._wav_path = None
                    self._last_save_body = None
                    overlay = self._ensure_overlay()
                    overlay.transcript = None
                    overlay.wav_path = None
                    overlay.suggested_title_text = None
                    overlay.set_idle()
                    notify("Voice Notes", "Note saved!", note_title)

                self._schedule_ui(on_success)

            except Exception as e:
                import traceback
                err = traceback.format_exc()
                debug(f"[voice-notes] Save error: {err}")

                def on_error():
                    self.state = AppState.POST_RECORDING
                    self._set_icon(ICON_IDLE)
                    self._ensure_overlay().push_error("Save failed", str(e))

                self._schedule_ui(on_error)

        threading.Thread(target=process, daemon=True).start()

    def discard_from_overlay(self):
        """Handle discard message from the webview."""
        debug("[voice-notes] Discarding")
        if self._wav_path:
            try:
                os.unlink(self._wav_path)
            except OSError:
                pass
        self._wav_path = None
        self._transcript = None
        self.state = AppState.IDLE
        self._set_icon(ICON_IDLE)
        self._ensure_overlay().set_idle()

    def copy_again(self):
        """Handle copy_again message from the webview."""
        if self._transcript:
            copy_to_clipboard(self._transcript)
            notify("Voice Notes", "Copied!", "Transcript copied to clipboard.")

    def done_from_overlay(self):
        """Handle done message from the webview (dictation mode)."""
        debug("[voice-notes] Done (dictation)")
        if self._wav_path:
            try:
                os.unlink(self._wav_path)
            except OSError:
                pass
        self._wav_path = None
        self._transcript = None
        self.state = AppState.IDLE
        self._set_icon(ICON_IDLE)
        self._ensure_overlay().set_idle()

    def open_notes(self, _=None):
        subprocess.run(["open", str(NOTES_DIR)])

    # --- Audio level push ---

    def _push_level(self, _timer):
        if self.state != AppState.RECORDING:
            return
        if self.overlay is None or not self.overlay.is_visible():
            return
        try:
            self.overlay.push_audio_level(self.recorder.last_level)
        except Exception:
            pass

    # --- Playback ---

    def playback_from_overlay(self):
        if not self._wav_path:
            return
        overlay = self._ensure_overlay()

        def on_finish():
            self._schedule_ui(lambda: overlay.push_playing_state(False))

        if self.player.play(self._wav_path, on_finish=on_finish):
            overlay.push_playing_state(True)

    def stop_playback_from_overlay(self):
        self.player.stop()
        if self.overlay:
            self.overlay.push_playing_state(False)

    # --- Recent notes ---

    def send_recent_notes(self):
        notes = list_recent_notes(limit=25)
        if self.overlay:
            self.overlay.push_recent_notes(notes)

    # --- Settings ---

    def send_settings(self):
        settings = {
            "default_mode": CONFIG.get("default_mode", "note"),
            "default_action": CONFIG.get("default_action", "clean_format"),
            "copy_to_clipboard": CONFIG.get("copy_to_clipboard", False),
            "hotkey": CONFIG.get("hotkey", "f1"),
            "whisper_model": WHISPER_MODEL,
            "sample_rate": SAMPLE_RATE,
            "vault_path": str(VAULT),
            "notes_folder": CONFIG.get("notes_folder", ""),
            "version": __version__,
        }
        if self.overlay:
            self.overlay.push_settings(settings)

    def save_settings_from_overlay(self, body):
        for key in ("default_mode", "default_action", "copy_to_clipboard", "hotkey"):
            if key in body:
                CONFIG[key] = body[key]
        save_config(CONFIG)
        notify("Voice Notes", "Settings saved", "Hotkey changes need a restart.")
        self.send_settings()

    # --- Retry ---

    def retry_from_overlay(self):
        if self._last_save_body and self._transcript:
            self.save_from_overlay(self._last_save_body)
        else:
            self._ensure_overlay().set_idle()
            self.state = AppState.IDLE


_INSTANCE_LOCK_PATH = Path("/tmp/voice-notes.lock")
_INSTANCE_PID_PATH  = Path("/tmp/voice-notes.pid")


def _acquire_single_instance_lock():
    """Ensure only one Voice Notes process runs at a time.

    If another instance already holds the lock, send SIGUSR1 to it (the
    running process will show its overlay) and exit this one cleanly.
    Returns the open lock-file handle on success; never returns on
    collision (the duplicate process exits)."""
    import fcntl, os as _os, signal as _signal, sys as _sys
    fh = open(_INSTANCE_LOCK_PATH, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another instance is running — ask it to surface the overlay, then bail.
        try:
            existing = int(_INSTANCE_PID_PATH.read_text().strip())
            _os.kill(existing, _signal.SIGUSR1)
            debug(f"[voice-notes] Already running (pid {existing}); signaled it to show overlay")
        except Exception as exc:
            debug(f"[voice-notes] Already running but couldn't signal existing instance: {exc}")
        _sys.exit(0)
    # Write our pid so future launches can find us
    _INSTANCE_PID_PATH.write_text(str(_os.getpid()))
    return fh


if __name__ == "__main__":
    debug("=== Voice Notes starting ===")
    _lock = _acquire_single_instance_lock()  # noqa: F841 (held for process lifetime)

    # SIGUSR1 from a second-launch attempt = "bring the overlay to front"
    import signal as _signal
    def _on_show_signal(signum, frame):
        try:
            app._schedule_ui(lambda: app.toggle_overlay() if not (app.overlay and app.overlay.is_visible()) else None)
        except Exception as exc:
            debug(f"[voice-notes] SIGUSR1 handler error: {exc}")
    _signal.signal(_signal.SIGUSR1, _on_show_signal)

    app = VoiceNotesApp()
    app.run()
