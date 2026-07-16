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
#     "pyobjc-framework-Speech>=10.0",
#     "pyobjc-framework-AVFoundation>=10.0",
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
    PREVIEW = "preview"
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
# engine: "whisper" (mlx-whisper) or "parakeet" (parakeet-mlx — ~5-10x faster,
# hallucination-resistant, English/EU languages only). Whisper is the fallback
# if the parakeet path fails for any reason.
ENGINE = CONFIG.get("engine", "whisper").lower()
PARAKEET_MODEL = CONFIG.get("parakeet_model", "mlx-community/parakeet-tdt-0.6b-v2")
ACTIVE_MODEL = PARAKEET_MODEL if ENGINE == "parakeet" else WHISPER_MODEL

# Force HuggingFace offline mode when the active model is already in the local
# cache. Recent huggingface_hub versions hang (sometimes 10+ minutes, 0% CPU)
# on their online repo check before falling back to cache; offline mode loads
# the cached model in seconds. Left online only when the model still needs
# its first download.
_hf_model_cache = Path(os.path.expanduser("~/.cache/huggingface/hub")) / (
    "models--" + ACTIVE_MODEL.replace("/", "--")
)
if _hf_model_cache.exists():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
SAMPLE_RATE = CONFIG.get("sample_rate", 16000)
INPUT_CHANNEL = CONFIG.get("input_channel", 1)
# input_device: device index (int) or name substring (str). None = system default.
INPUT_DEVICE = CONFIG.get("input_device", None)
# Max recording length in seconds before auto-stop (default 10 min).
# Minutes before showing the "still recording?" reminder in the overlay.
RECORD_REMINDER_MINUTES = CONFIG.get("record_reminder_minutes", 30)
# Max duration to attempt transcription — refuse anything longer (default 2 hours).
MAX_TRANSCRIBE_SECONDS = CONFIG.get("max_transcribe_seconds", 7200)
# Dictation shows the transcript for this long (with Esc-to-cancel) before
# pasting at the cursor. 0 = paste immediately with no preview.
PASTE_PREVIEW_SECONDS = CONFIG.get("paste_preview_seconds", 1.5)
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
        self.tap = None        # optional callable(chunk) for live captioning

    def _callback(self, indata, frames, time_info, status):
        ch = min(INPUT_CHANNEL - 1, indata.shape[1] - 1)
        chunk = indata[:, ch:ch + 1].copy()
        self.audio_queue.put(chunk)
        # Optional tap for live captioning during dictation
        tap = self.tap
        if tap:
            try:
                tap(chunk)
            except Exception:
                pass
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

    @staticmethod
    def _resolve_device(device_cfg):
        """Resolve INPUT_DEVICE config value to a sounddevice device index or None.

        Accepts:
          None      → system default
          int       → use that index directly
          str       → find first input device whose name contains that substring (case-insensitive)
        """
        if device_cfg is None:
            return None
        if isinstance(device_cfg, int):
            return device_cfg
        # String: match by name
        needle = device_cfg.lower()
        for i, info in enumerate(sd.query_devices()):
            if int(info.get('max_input_channels', 0)) > 0 and needle in info['name'].lower():
                debug(f"[voice-notes] Resolved device '{device_cfg}' → [{i}] {info['name']}")
                return i
        debug(f"[voice-notes] WARNING: input_device '{device_cfg}' not found, using default")
        return None

    def _open_stream(self, device=None):
        """Open an InputStream on the given device index (None = system default)."""
        dev_idx = sd.default.device[0] if device is None else device
        info = sd.query_devices(dev_idx, 'input')
        max_ch = int(info.get('max_input_channels', 0))
        ch = min(INPUT_CHANNEL, max_ch) if max_ch > 0 else INPUT_CHANNEL
        debug(f"[voice-notes] Opening stream: [{dev_idx}] {info['name']}  "
              f"ch={ch}/{max_ch}  sr={self.sample_rate}")
        stream = sd.InputStream(
            device=device,
            samplerate=self.sample_rate,
            channels=ch,
            dtype="float32",
            callback=self._callback,
        )
        stream.start()
        return stream

    def start(self):
        self.audio_queue = queue.Queue()
        self.recording = True
        self.start_time = time.time()
        self.last_level = 0.0
        last_exc = None

        # Try configured device first, then fall back through all input devices.
        preferred = self._resolve_device(INPUT_DEVICE)
        candidates = [preferred] if preferred is not None else []
        # Append all other input devices as fallbacks (skipping already-tried preferred)
        candidates += [
            i for i, info in enumerate(sd.query_devices())
            if int(info.get('max_input_channels', 0)) > 0 and i != preferred
        ]

        for device in candidates:
            try:
                self.stream = self._open_stream(device=device)
                return
            except Exception as exc:
                debug(f"[voice-notes] Device {device} failed: {exc}")
                last_exc = exc

        self.recording = False
        raise RuntimeError(
            f"Could not open any microphone. Check System Settings → Privacy & Security → Microphone. "
            f"Last error: {last_exc}"
        )

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
        tmp.close()   # sf.write opens its own handle; don't leak this fd
        sf.write(tmp.name, audio_data, self.sample_rate)
        return tmp.name, int(duration)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

_whisper_loaded = False
# MLX can't run two transcriptions at once (each would load its own copy of
# the model); serialize all calls — including the startup pre-warm.
_whisper_lock = threading.Lock()
# Set when a transcription worker times out while holding _whisper_lock: the
# abandoned thread may never release it, so every later transcription would
# block forever. Fail fast with a clear message instead of pretending.
_engine_poisoned = False


_parakeet_model = None


def _load_wav_mono_16k(wav_path):
    """Read a WAV as mono float32 at 16 kHz (nearest-neighbor resample)."""
    import numpy as np
    data, sr = sf.read(wav_path, dtype="float32")
    if sr != 16000:
        n_samples = int(len(data) * 16000 / sr)
        indices = np.arange(n_samples) * sr / 16000
        indices = np.clip(indices.astype(int), 0, len(data) - 1)
        data = data[indices]
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data


def _transcribe_whisper(data):
    import mlx_whisper
    result = mlx_whisper.transcribe(data, path_or_hf_repo=WHISPER_MODEL, language="en")
    return result.get("text", "").strip()


def _transcribe_parakeet(wav_path):
    """Transcribe with parakeet-mlx using its chunked file API.

    chunk_duration is essential: feeding a whole long recording through the
    model in one generate() call blows past Metal's GPU limits and SIGABRTs
    the process (observed on a 53-minute file). parakeet-mlx's transcribe()
    processes overlapping chunks and merges on token timestamps. Its stock
    audio loader shells out to ffmpeg, which isn't installed — swap in a
    soundfile-based loader.
    """
    global _parakeet_model
    import mlx.core as mx
    from parakeet_mlx import from_pretrained
    import parakeet_mlx.parakeet as _pk

    def _sf_loader(filename, sampling_rate, dtype=None):
        # Always float32 regardless of requested dtype: get_logmel's
        # view(complex, original_dtype) trick assumes 4-byte elements —
        # bfloat16 audio doubles the frequency bins and crashes the matmul.
        data = _load_wav_mono_16k(str(filename))   # we record 16k mono WAVs
        return mx.array(data)
    _pk.load_audio = _sf_loader

    if _parakeet_model is None:
        _parakeet_model = from_pretrained(PARAKEET_MODEL)
    result = _parakeet_model.transcribe(
        wav_path, chunk_duration=120.0, overlap_duration=15.0,
    )
    return result.text.strip()


def transcribe(wav_path):
    global _whisper_loaded

    if _engine_poisoned:
        raise RuntimeError(
            "The transcription engine hung earlier and can't recover in-place. "
            "Quit Voice Notes from the menu bar and reopen it."
        )

    if not _whisper_loaded:
        notify("Voice Notes", f"Loading {ENGINE} model...",
               "First transcription may take a moment.")
        _whisper_loaded = True

    with _whisper_lock:
        if ENGINE == "parakeet":
            try:
                return _transcribe_parakeet(wav_path)
            except Exception as exc:
                debug(f"[voice-notes] Parakeet failed, falling back to Whisper: {exc!r}")
                # The offline flag is keyed on the parakeet cache — if Whisper
                # was never downloaded, lift it for this fallback download.
                whisper_cache = Path(os.path.expanduser("~/.cache/huggingface/hub")) / (
                    "models--" + WHISPER_MODEL.replace("/", "--")
                )
                if not whisper_cache.exists() and os.environ.get("HF_HUB_OFFLINE"):
                    os.environ.pop("HF_HUB_OFFLINE", None)
                    debug("[voice-notes] Lifted HF_HUB_OFFLINE for Whisper fallback download")
        return _transcribe_whisper(_load_wav_mono_16k(wav_path))


# NOTE: do not add a startup model pre-warm thread. Importing/loading MLX
# in a background thread during app init deadlocks (wedged at ~124MB RSS,
# never loads), and any lock around it then blocks real transcriptions
# behind the wedged thread. Lazy-load on first transcription instead —
# the first-run timeout already includes +300s headroom for the load.


class LiveCaption:
    """Live captions during dictation via parakeet-mlx's streaming API.

    The Recorder's audio callback feeds float32 mono chunks through feed();
    a worker thread batches them to ~1s (streaming runs ~5x realtime at that
    batch size; smaller batches waste compute on context reprocessing) and
    pushes partial text through on_text. Runs entirely in-process on MLX —
    no AVAudioEngine, so no Core Audio conflict with PortAudio.
    """

    def __init__(self, on_text):
        self._on_text = on_text
        self._q = queue.Queue()
        self._stop = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def feed(self, chunk):
        if not self._stop:
            self._q.put(chunk)

    def stop(self):
        """Signal the worker to drain and exit. Blocks briefly; the final
        full-file transcription waits on _whisper_lock anyway."""
        self._stop = True
        if self._thread:
            self._thread.join(timeout=8)

    def _run(self):
        global _parakeet_model
        if _engine_poisoned:
            debug("[voice-notes] Live captions skipped — engine poisoned")
            return
        try:
            import numpy as np
            import mlx.core as mx
            from parakeet_mlx import from_pretrained

            with _whisper_lock:
                if _parakeet_model is None:
                    _parakeet_model = from_pretrained(PARAKEET_MODEL)
                buf, buffered, last_emit = [], 0, 0.0
                with _parakeet_model.transcribe_stream(context_size=(256, 256)) as stream:
                    while not (self._stop and self._q.empty()):
                        try:
                            chunk = self._q.get(timeout=0.15)
                        except queue.Empty:
                            continue
                        data = chunk[:, 0] if getattr(chunk, "ndim", 1) > 1 else chunk
                        buf.append(data)
                        buffered += len(data)
                        if buffered >= 16000:   # ~1s batches
                            stream.add_audio(mx.array(np.concatenate(buf)))
                            buf, buffered = [], 0
                            now = time.time()
                            if now - last_emit > 0.5:
                                self._on_text(stream.result.text)
                                last_emit = now
                    if buf:
                        stream.add_audio(mx.array(np.concatenate(buf)))
                    self._on_text(stream.result.text)
        except Exception as exc:
            debug(f"[voice-notes] Live caption error: {exc!r}")


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
    # Prompt goes on stdin: multi-hour transcripts can exceed ARG_MAX as argv.
    cmd = [claude_bin, "-p"]
    if project:
        cmd.extend(["--project", project])

    def _run(cmd):
        return subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=300, cwd=str(VAULT),
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
            [claude_bin, "-p"], input=prompt,
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
            proc = self.proc   # capture: self.proc may be swapped/None'd by stop()

            def watch():
                proc.wait()
                # Only report finish if we're still the active playback
                if self.proc is proc:
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
    # Never silently overwrite: filenames are minute-granular, so two quick
    # saves (or same-titled notes) can collide — suffix a counter instead.
    if note_path.exists():
        stem = note_path.stem
        n = 2
        while (NOTES_DIR / f"{stem} {n}.md").exists():
            n += 1
        filename = f"{stem} {n}.md"
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


def start_hotkey_listener(dictation_callback, note_callback):
    """Listen for F1 (dictation) and F2 (note) hotkeys.

    Falls back to the legacy single-hotkey config if present so existing
    users aren't broken. New default: F1=dictation, F2=note.
    """
    from pynput import keyboard

    # Build list of (modifiers, key, callback) tuples to watch
    bindings = []

    # Check for legacy single-hotkey config — keep it working as note mode
    legacy = CONFIG.get("hotkey", "")
    if legacy and legacy.lower() not in ("f1", "f2"):
        mods, k = parse_hotkey(legacy)
        if k:
            bindings.append((mods, k, note_callback))

    # F1 → dictation, F2 → note (always registered)
    _, f1 = parse_hotkey("f1")
    _, f2 = parse_hotkey("f2")
    if f1:
        bindings.append((set(), f1, dictation_callback))
    if f2:
        bindings.append((set(), f2, note_callback))

    if not bindings:
        print("Warning: no valid hotkeys configured.")
        return

    current_modifiers = set()

    def on_press(k):
        from pynput.keyboard import Key
        if k in (Key.cmd, Key.ctrl, Key.alt, Key.shift,
                 Key.cmd_r, Key.ctrl_r, Key.alt_r, Key.shift_r):
            current_modifiers.add(k)
        for mods, trigger, cb in bindings:
            # Normalise cmd_l/cmd_r etc. for modifier check
            required = mods
            if current_modifiers >= required and k == trigger:
                cb()

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

    # Drag-handle rect in CSS coordinates (top-left origin), reported live by
    # the overlay JS whenever the visible screen changes (see reportDragZone
    # in overlay.html). Only clicks inside this rect start a window drag, so
    # the drag zone always matches the actual title element and can never
    # collide with buttons. Fallback legacy strip is used until the first
    # report arrives.
    HEADER_DRAG_HEIGHT = 52
    HEADER_LEFT_INSET  = 110
    HEADER_RIGHT_INSET = 200

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return False

    def _in_drag_zone(self, point):
        """point is in window coords (bottom-left origin)."""
        frame_size = self.frame().size
        rect = getattr(self, "_drag_rect_css", None)
        if rect:
            css_x = point.x
            css_y = frame_size.height - point.y   # convert to top-left origin
            return (rect["x"] <= css_x <= rect["x"] + rect["w"] and
                    rect["y"] <= css_y <= rect["y"] + rect["h"])
        # Legacy fallback before JS reports a rect
        in_header = point.y >= (frame_size.height - self.HEADER_DRAG_HEIGHT)
        in_mid = (self.HEADER_LEFT_INSET < point.x <
                  (frame_size.width - self.HEADER_RIGHT_INSET))
        return in_header and in_mid

    def sendEvent_(self, event):
        if event.type() == NSEventTypeLeftMouseDown:
            try:
                if self._in_drag_zone(event.locationInWindow()):
                    # performWindowDragWithEvent: runs the full drag loop;
                    # it returns immediately after starting.
                    self.performWindowDragWithEvent_(event)
                    return
            except Exception as exc:
                debug(f"[voice-notes] drag intercept error: {exc}")
        objc.super(ClickablePanel, self).sendEvent_(event)


class NavDelegate(NSObject):
    """WKNavigationDelegate to log webview load events."""

    def init(self):
        self = objc.super(NavDelegate, self).init()
        if self is None:
            return None
        self.panel = None  # OverlayPanel — set after construction
        return self

    def webView_didFinishNavigation_(self, webview, navigation):
        debug("WebView finished loading!")
        webview.evaluateJavaScript_completionHandler_(
            "document.querySelector('.record-btn') ? 'record-btn-found' : 'record-btn-NOT-found'",
            lambda result, error: debug(f"JS test: result={result}, error={error}"),
        )
        # Flush any JS that was queued while the page was still loading
        if self.panel is not None:
            self.panel._on_webview_ready()

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
        """Primary JS→Python transport. postMessage delivery is ordered and
        never coalesced, unlike the title-KVO fallback (which WebKit can
        collapse under rapid successive sends, dropping messages)."""
        try:
            body = dict(message.body())
        except (TypeError, ValueError):
            return
        if self.panel is None:
            return
        seq = body.get("_seq", 0)
        if seq <= self.panel._last_msg_seq:
            return  # duplicate (e.g. also delivered via title fallback)
        self.panel._last_msg_seq = seq
        if body.get("action") != "drag_zone":   # too chatty to log
            debug(f"Bridge message: {body}")
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
        # JS evaluated before the WebView finishes loading is silently
        # dropped — queue until didFinishNavigation flushes it in order.
        self._js_ready = False
        self._js_queue = []

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
        # The Molten design is a fixed 420x540 grid — don't let resizing
        # shrink below it (compact pill mode temporarily lowers this).
        self.panel.setMinSize_((self.WIDTH, self.HEIGHT))

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

        # Navigation delegate for load diagnostics + queued-JS flush
        self.nav_delegate = NavDelegate.alloc().init()
        self.nav_delegate.panel = self
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
        elif action == "process":
            self.app.process_from_overlay(body)
        elif action == "save_processed":
            self.app.save_processed_from_overlay(body)
        elif action == "copy_preview":
            self.app.copy_preview_from_overlay(body)
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
        elif action == "drag_zone":
            # JS-reported drag-handle rect (CSS px, top-left origin)
            try:
                self.panel._drag_rect_css = {
                    "x": float(body.get("x", 0)), "y": float(body.get("y", 0)),
                    "w": float(body.get("w", 0)), "h": float(body.get("h", 0)),
                }
            except (TypeError, ValueError):
                pass
        elif action == "cancel_paste":
            self.app.cancel_paste_from_overlay()
        elif action == "back_to_edit":
            # UI went preview → editor; mirror it in the Python state machine
            if self.app.state == AppState.PREVIEW:
                self.app.state = AppState.POST_RECORDING
        elif action == "cancel_recording":
            self.app.cancel_recording_from_overlay()
        elif action == "open_settings":
            # Jump straight to the Microphone privacy pane
            subprocess.run(
                ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"],
                check=False,
            )

    def _eval_js(self, script):
        """Evaluate JavaScript in the webview (queued until the page loads)."""
        if not self._js_ready:
            self._js_queue.append(script)
            return
        if self.webview:
            self.webview.evaluateJavaScript_completionHandler_(script, None)

    def _on_webview_ready(self):
        """Called from the navigation delegate when the HTML finishes loading."""
        self._js_ready = True
        queued, self._js_queue = self._js_queue, []
        debug(f"[voice-notes] WebView ready — flushing {len(queued)} queued JS calls")
        for script in queued:
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
            .replace(" ", "\\u2028")   # JS line separators — legal in
            .replace(" ", "\\u2029")   # Python strings, fatal in JS literals
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

        # Don't clobber an in-progress note's action/clipboard choices when
        # the overlay is merely re-shown mid-flow.
        if self.app.state not in (AppState.POST_RECORDING, AppState.PREVIEW):
            default_action = CONFIG.get("default_action", "clean_format")
            self._eval_js(f"setDefaultAction('{default_action}')")
            clipboard = "true" if CONFIG.get("copy_to_clipboard", False) else "false"
            self._eval_js(f"setClipboardDefault({clipboard})")

        projects = CONFIG.get("projects", [{"name": "Default", "flag": None}])
        projects_json = json.dumps(projects)
        self._eval_js(f"setProjects({projects_json})")

        # Real telemetry
        self._eval_js(f"setSampleRate({SAMPLE_RATE})")
        model_escaped = self._js_escape(ACTIVE_MODEL)
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
        # Errors always show the full panel, never the compact pill
        self.set_compact(False)
        title_e = self._js_escape(title)
        msg_e = self._js_escape(message)
        self._eval_js(f"showError('{title_e}', '{msg_e}')")

    # Compact "dictation pill" geometry
    COMPACT_WIDTH = 360
    COMPACT_HEIGHT = 76

    def set_compact(self, compact):
        """Toggle between the full 420x540 panel and a slim pill near the
        menu bar (used for dictation so it doesn't take over the screen)."""
        if getattr(self, "_compact", False) == compact:
            return
        self._compact = compact
        screen = NSScreen.mainScreen().frame()
        if compact:
            self._saved_frame = self.panel.frame()
            self.panel.setMinSize_((self.COMPACT_WIDTH, self.COMPACT_HEIGHT))
            x = (screen.size.width - self.COMPACT_WIDTH) / 2
            y = screen.size.height - self.COMPACT_HEIGHT - 44
            self.panel.setFrame_display_animate_(
                ((x, y), (self.COMPACT_WIDTH, self.COMPACT_HEIGHT)), True, True,
            )
        else:
            self.panel.setMinSize_((self.WIDTH, self.HEIGHT))
            frame = getattr(self, "_saved_frame", None)
            if frame is not None:
                self.panel.setFrame_display_animate_(frame, True, True)
            else:
                x = (screen.size.width - self.WIDTH) / 2
                y = screen.size.height - self.HEIGHT - 60
                self.panel.setFrame_display_animate_(
                    ((x, y), (self.WIDTH, self.HEIGHT)), True, True,
                )
        self._eval_js(f"setCompactMode({'true' if compact else 'false'})")
        debug(f"[voice-notes] Compact mode: {compact}")

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

    def push_preview(self, content, action):
        data = json.dumps({"content": content, "action": action})
        self._eval_js(f"setPreviewContent({data})")


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
        self._auto_stop_timer = None
        self._live_caption = None
        self._preview_cache = {}     # action key → processed content
        self._last_preview_content = None  # what's on the preview screen now
        self._last_process_body = None
        self._paste_timer = None
        self._paste_cancelled = False
        self._recording_mode = "note"
        self._note_gen = 0   # increments per take; gates async title suggestions

        # Start hotkey listener — F1=dictation, F2=note
        start_hotkey_listener(self._hotkey_dictation, self._hotkey_note)

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

    def _capture_previous_app(self):
        """Remember the frontmost app so dictation can restore focus before
        pasting. Never capture ourselves (e.g. when recording starts from a
        click inside the overlay) — pasting into our own webview is worse
        than not pasting at all."""
        from AppKit import NSWorkspace
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None and front.processIdentifier() == os.getpid():
            front = None
        self._previous_app = front
        debug(f"[voice-notes] Previous app: {front.bundleIdentifier() if front else 'none'}")

    # --- Hotkey ---

    def _hotkey_dictation(self):
        self._schedule_ui(lambda: self._handle_hotkey(force_mode="dictation"))

    def _hotkey_note(self):
        self._schedule_ui(lambda: self._handle_hotkey(force_mode="note"))

    def _handle_hotkey(self, force_mode=None):
        debug(f"[voice-notes] Hotkey pressed, state={self.state.value}, force_mode={force_mode}")
        overlay = self._ensure_overlay()

        if self.state == AppState.IDLE:
            # Capture frontmost app BEFORE the overlay steals focus.
            self._capture_previous_app()

            # Switch to the mode for this hotkey
            if force_mode and overlay.mode != force_mode:
                overlay.mode = force_mode
                overlay._eval_js(f"setModeFromPython('{force_mode}')")

            # Dictation records in the slim pill; note mode gets the full panel
            overlay.set_compact(overlay.mode == "dictation")

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
            # Same setup as the hotkey path so blob-tap dictation behaves
            # identically (focus capture for the paste, pill for dictation).
            overlay = self._ensure_overlay()
            self._capture_previous_app()
            overlay.set_compact(overlay.mode == "dictation")
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
        self._cancel_paste_timer()   # a stale countdown must never fire mid-take
        overlay = self._ensure_overlay()
        # Freeze the mode for this take: toggling the mode switch while
        # transcribing must not reroute the result (note → surprise paste).
        self._recording_mode = overlay.mode
        self.state = AppState.RECORDING
        try:
            self.recorder.start()
        except Exception as exc:
            err_str = str(exc)
            # PaErrorCode -9986 = paInternalError — usually a stale PortAudio
            # device list after Core Audio hiccups. Re-initialise PortAudio
            # in-process and retry once. (sudo killall coreaudiod can't work
            # from a GUI app — no terminal to prompt for the password.)
            if "-9986" in err_str:
                debug("[voice-notes] PortAudio internal error — reinitialising and retrying…")
                try:
                    sd._terminate()
                    time.sleep(0.5)
                    sd._initialize()
                    self.recorder.start()
                    debug("[voice-notes] Retry after PortAudio reset succeeded")
                except Exception as retry_exc:
                    self.state = AppState.IDLE
                    debug(f"[voice-notes] Retry failed: {retry_exc}")
                    overlay = self._ensure_overlay()
                    overlay.push_error(
                        "Microphone Error",
                        "Core Audio is in a bad state. Run  sudo killall coreaudiod  in Terminal, then try again."
                    )
                    return
            else:
                self.state = AppState.IDLE
                debug(f"[voice-notes] start_recording error: {exc}")
                overlay = self._ensure_overlay()
                overlay.push_error("Microphone Error", err_str)
                return
        self._set_icon(ICON_RECORDING)

        overlay = self._ensure_overlay()
        overlay.set_recording()
        if not overlay.is_visible():
            NSApp.activateIgnoringOtherApps_(True)
            overlay.panel.makeKeyAndOrderFront_(None)

        # After RECORD_REMINDER_MINUTES, pop the overlay and show a "still recording?" banner
        reminder_secs = RECORD_REMINDER_MINUTES * 60
        def _remind(_timer):
            if self.state != AppState.RECORDING:
                return
            # rumps.Timer fires once immediately on start — ignore that one.
            elapsed = time.time() - (self.recorder.start_time or 0)
            if elapsed < reminder_secs - 5:
                return
            mins = RECORD_REMINDER_MINUTES
            debug(f"[voice-notes] Long-recording reminder at {mins} min")
            ov = self._ensure_overlay()
            if not ov.is_visible():
                ov.show()
            NSApp.activateIgnoringOtherApps_(True)
            ov.panel.makeKeyAndOrderFront_(None)
            ov._eval_js(f"showLongRecordingReminder({mins})")
        self._auto_stop_timer = rumps.Timer(_remind, reminder_secs)
        self._auto_stop_timer.start()

        # Live captions while dictating (parakeet streaming only)
        if self._recording_mode == "dictation" and ENGINE == "parakeet":
            def on_text(text):
                snippet = overlay._js_escape((text or "").strip())
                self._schedule_ui(lambda: overlay._eval_js(f"setLiveTranscript('{snippet}')"))
            self._live_caption = LiveCaption(on_text)
            self._live_caption.start()
            self.recorder.tap = self._live_caption.feed

        debug("[voice-notes] Recording started")

    def cancel_recording_from_overlay(self):
        """Stop the mic and throw the audio away — no transcription."""
        if self.state != AppState.RECORDING:
            return
        debug("[voice-notes] Recording cancelled")
        self._teardown_live_caption()
        timer = getattr(self, '_auto_stop_timer', None)
        if timer:
            try:
                timer.stop()
            except Exception:
                pass
            self._auto_stop_timer = None
        wav_path, _duration = self.recorder.stop()
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
        self.state = AppState.IDLE
        self._set_icon(ICON_IDLE)
        overlay = self._ensure_overlay()
        overlay.set_idle()
        # A cancelled dictation pill should disappear entirely
        if getattr(overlay, "_compact", False):
            overlay.hide()
            overlay.set_compact(False)

    def _teardown_live_caption(self):
        """Detach the mic tap and let the caption worker drain on its own
        thread (it holds _whisper_lock until done, which naturally serializes
        it before the final transcription)."""
        self.recorder.tap = None
        lc = getattr(self, '_live_caption', None)
        self._live_caption = None
        if lc:
            threading.Thread(target=lc.stop, daemon=True).start()

    def stop_recording(self):
        self._teardown_live_caption()
        # Cancel reminder timer and hide banner if still visible
        timer = getattr(self, '_auto_stop_timer', None)
        if timer:
            try:
                timer.stop()
            except Exception:
                pass
            self._auto_stop_timer = None
        try:
            self._ensure_overlay()._eval_js("hideLongRecordingReminder()")
        except Exception:
            pass

        wav_path, duration = self.recorder.stop()
        self._set_icon(ICON_PROCESSING)
        self.state = AppState.TRANSCRIBING
        self._note_gen += 1   # invalidates any in-flight title suggestion

        overlay = self._ensure_overlay()

        if not wav_path or duration < 1:
            self.state = AppState.IDLE
            self._set_icon(ICON_IDLE)
            overlay.set_idle()
            if wav_path:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            notify("Voice Notes", "Too short", "Recording was too short to process.")
            return

        if duration > MAX_TRANSCRIBE_SECONDS:
            self.state = AppState.IDLE
            self._set_icon(ICON_IDLE)
            overlay.set_idle()
            mins = int(duration // 60)
            limit = MAX_TRANSCRIBE_SECONDS // 60
            # Deliberately keep the WAV — it's the user's only copy. The
            # startup sweep reclaims it after 24h if they don't act.
            overlay.push_error(
                "Recording Too Long",
                f"Recording was {mins} min — max is {limit} min. The audio is "
                f"kept for 24h at: {wav_path}. Increase max_transcribe_seconds "
                "in config.json and use Retry to transcribe it."
            )
            self._wav_path = wav_path
            self._duration = duration
            debug(f"[voice-notes] Refusing to transcribe {duration}s recording (limit={MAX_TRANSCRIBE_SECONDS}s)")
            return

        self._start_transcription(wav_path, duration)

    def _start_transcription(self, wav_path, duration):
        """Kick off (or retry) transcription of a finished recording."""
        overlay = self._ensure_overlay()
        self.state = AppState.TRANSCRIBING
        self._set_icon(ICON_PROCESSING)
        self._wav_path = wav_path
        self._duration = duration
        overlay.wav_path = wav_path
        overlay.duration = duration
        overlay.set_transcribing()
        # First transcription of this app launch also loads the model —
        # show that explicitly so it doesn't read as "stuck".
        overlay._eval_js(
            f"setTranscribingPhase('{'transcribing' if _whisper_loaded else 'loading'}')"
        )

        debug(f"[voice-notes] Stopped, transcribing {wav_path} ({duration}s)...")

        # Estimate timeout: 5× realtime, minimum 60s, max 600s.
        # First transcription also loads the Whisper model, which can take
        # a couple of minutes on its own — give it generous headroom.
        transcribe_timeout = max(60, min(600, int(duration * 5)))
        if not _whisper_loaded:
            transcribe_timeout += 300

        def do_transcribe():
            import concurrent.futures
            debug(f"[voice-notes] Transcribing (timeout={transcribe_timeout}s)…")
            try:
                # No `with` block: context-manager exit calls shutdown(wait=True),
                # which would block on the still-running transcribe and defeat
                # the timeout. shutdown(wait=False) abandons the worker instead.
                ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = ex.submit(transcribe, wav_path)
                try:
                    transcript = future.result(timeout=transcribe_timeout)
                except concurrent.futures.TimeoutError:
                    global _engine_poisoned
                    _engine_poisoned = True   # abandoned worker still holds the lock
                    ex.shutdown(wait=False)
                    debug(f"[voice-notes] Transcription timed out after {transcribe_timeout}s — engine poisoned")
                    self._schedule_ui(lambda: self._transcription_error(
                        f"Transcription hung ({transcribe_timeout}s). "
                        "Quit Voice Notes from the menu bar and reopen it — "
                        "your recording is kept and Retry will re-run it."
                    ))
                    return
                ex.shutdown(wait=False)

                debug(f"[voice-notes] Transcript: {transcript[:200] if transcript else '(empty)'}")

                if not transcript:
                    self._schedule_ui(self._transcription_failed)
                    return

                self._transcript = transcript
                overlay.transcript = transcript

                # Route by the mode captured at recording start, not the live
                # toggle — switching modes mid-transcription must not reroute.
                if getattr(self, "_recording_mode", overlay.mode) == "dictation":
                    copy_to_clipboard(transcript)
                    self._schedule_ui(lambda: self._show_dictation_result(transcript))
                else:
                    self._schedule_ui(self._show_post_recording)
                    threading.Thread(
                        target=self._suggest_title,
                        args=(transcript, self._note_gen), daemon=True,
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
        self._preview_cache = {}   # fresh transcript → fresh previews
        self._last_preview_content = None
        self._set_icon(ICON_IDLE)
        self._ensure_overlay().set_post_note(
            transcript=self._transcript, duration=self._duration,
        )
        debug("[voice-notes] Showing post-recording UI")

    def _show_dictation_result(self, transcript):
        """In dictation mode: preview the transcript briefly (Esc cancels),
        then hide the overlay, restore focus, and paste at the cursor."""
        self._set_icon(ICON_IDLE)
        overlay = self._ensure_overlay()
        prev = self._previous_app
        self._previous_app = None
        self._paste_cancelled = False

        def do_paste():
            # Guard on state as well as the flag: Done/discard/× or a new
            # recording moves state off POST_RECORDING, and a stale timer
            # must never paste into whatever is frontmost.
            if self._paste_cancelled or self.state != AppState.POST_RECORDING:
                debug("[voice-notes] Paste skipped (cancelled or state moved on)")
                return
            self.state = AppState.IDLE
            overlay.hide()
            overlay.set_compact(False)   # restore full panel for next open
            debug("[voice-notes] Dictation: typing at cursor")
            wav = self._wav_path
            self._wav_path = None

            def paste_and_cleanup():
                self._type_at_cursor(transcript, prev)
                if wav:
                    try:
                        os.unlink(wav)
                    except OSError:
                        pass

            threading.Thread(target=paste_and_cleanup, daemon=True).start()

        if PASTE_PREVIEW_SECONDS <= 0:
            do_paste()
            return

        # Show the transcript with a countdown bar; Esc / Cancel aborts the paste
        self.state = AppState.POST_RECORDING
        overlay.set_post_dictation(transcript)
        overlay._eval_js(f"startPasteCountdown({PASTE_PREVIEW_SECONDS})")
        self._paste_timer = threading.Timer(
            PASTE_PREVIEW_SECONDS, lambda: self._schedule_ui(do_paste)
        )
        self._paste_timer.start()

    def _cancel_paste_timer(self):
        self._paste_cancelled = True
        timer = getattr(self, '_paste_timer', None)
        self._paste_timer = None
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass

    def cancel_paste_from_overlay(self):
        """User hit Esc/Cancel during the paste countdown — keep the transcript
        on screen (and on the clipboard) but don't type it anywhere."""
        debug("[voice-notes] Paste cancelled by user")
        self._cancel_paste_timer()
        # Expand from the pill to the full panel so the transcript is reviewable
        self._ensure_overlay().set_compact(False)

    def _type_at_cursor(self, text, prev_app):
        """Copy text to clipboard, restore focus to prev_app, then paste with Cmd+V.

        Uses pynput for the keystroke — covered by the Accessibility grant on
        this process's python binary. (osascript/System Events needs a separate
        Automation permission that launchd-spawned processes don't get.)
        """
        try:
            # 1. Put text on clipboard
            copy_to_clipboard(text)

            # 2. Restore focus to the app that was frontmost before the overlay.
            # If we never captured one, do NOT paste blind into whatever
            # happens to be frontmost — leave it on the clipboard instead.
            if not prev_app:
                debug("[voice-notes] No previous app — skipping paste, clipboard only")
                notify("Voice Notes", "Transcript copied",
                       "No target window — press Cmd+V where you want it.")
                return
            from AppKit import NSApplicationActivateIgnoringOtherApps
            prev_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(0.25)  # let the window manager transfer focus

            # 3. Paste via System Events. Under launchd, /usr/bin/osascript
            # itself must be granted Accessibility (error 1002 otherwise).
            # Do NOT use pynput Controller here — posting CGEvents from a
            # background thread crashes the whole process.
            result = subprocess.run(
                ['osascript', '-e',
                 'tell application "System Events" to keystroke "v" using command down'],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                debug(f"[voice-notes] osascript paste error: {result.stderr.strip()!r}")
                notify("Voice Notes", "Paste blocked",
                       "Transcript is on the clipboard — press Cmd+V. "
                       "To fix: add /usr/bin/osascript to Accessibility.")
            else:
                debug(f"[voice-notes] Pasted {len(text)} chars at cursor")
        except Exception as exc:
            debug(f"[voice-notes] _type_at_cursor error: {exc!r}")

    def _suggest_title(self, transcript, gen):
        title = suggest_title(transcript)
        if not title:
            return

        def apply():
            # A slow suggestion for note A must never attach to note B —
            # only apply if we're still on the same recording generation.
            if gen != self._note_gen:
                debug(f"[voice-notes] Dropping stale title suggestion: {title}")
                return
            debug(f"[voice-notes] Title suggestion: {title}")
            self._ensure_overlay().update_title_suggestion(title)

        self._schedule_ui(apply)

    # --- Preview (process without saving) ---

    @staticmethod
    def _preview_key(body):
        action = body.get("actionType", "clean_format")
        custom = (body.get("customPrompt") or "").strip()
        return f"{action}|{custom}" if action == "custom" and custom else action

    def process_from_overlay(self, body):
        """Run the selected action through Claude and preview the result —
        nothing is written to the vault until save_processed."""
        self._last_process_body = body
        overlay = self._ensure_overlay()

        def after_fetch(result, error):
            edited = result if isinstance(result, str) else None
            if error:
                debug(f"[voice-notes] Transcript fetch error: {error}")
            self._run_process(body, edited)

        overlay.webview.evaluateJavaScript_completionHandler_(
            "(function(){var t=document.getElementById('transcript-edit');"
            "return t ? t.value : '';})()",
            after_fetch,
        )

    def _run_process(self, body, edited_transcript):
        if self.state == AppState.SAVING:
            debug("[voice-notes] Process request ignored — already processing")
            return
        action_type = body.get("actionType", "clean_format")
        project = body.get("project", "") or None
        custom_prompt = body.get("customPrompt", "") or None
        overlay = self._ensure_overlay()

        if edited_transcript and edited_transcript.strip():
            if edited_transcript.strip() != (self._transcript or ""):
                self._preview_cache = {}   # transcript edited → previews stale
            self._transcript = edited_transcript.strip()

        key = self._preview_key(body)
        cached = self._preview_cache.get(key)
        if cached is not None:
            debug(f"[voice-notes] Preview cache hit: {key}")
            self._last_preview_content = cached
            self.state = AppState.PREVIEW
            overlay.push_preview(cached, action_type)
            return

        self.state = AppState.SAVING          # reuse the processing screen
        self._set_icon(ICON_PROCESSING)
        overlay.set_saving()
        transcript = self._transcript
        debug(f"[voice-notes] Processing preview: action={action_type}, tlen={len(transcript or '')}")

        def process():
            content = process_with_claude(
                transcript, action=action_type, project=project,
                custom_prompt=custom_prompt,
            )

            def on_done():
                self._preview_cache[key] = content
                self._last_preview_content = content
                self.state = AppState.PREVIEW
                self._set_icon(ICON_IDLE)
                overlay.push_preview(content, action_type)

            self._schedule_ui(on_done)

        threading.Thread(target=process, daemon=True).start()

    def save_processed_from_overlay(self, body):
        """Commit the previewed content to the vault (no re-processing)."""
        content = self._preview_cache.get(self._preview_key(body))
        if content is None:
            # No preview for this action (shouldn't happen) — full pipeline
            self.save_from_overlay(body)
            return

        title = body.get("title", "").strip() or None
        if not title:
            title = self._ensure_overlay().suggested_title_text
        copy_clip = body.get("copyClipboard", False)
        CONFIG["copy_to_clipboard"] = copy_clip
        save_config(CONFIG)
        if copy_clip:
            copy_to_clipboard(content)

        self.state = AppState.SAVING
        self._set_icon(ICON_PROCESSING)
        overlay = self._ensure_overlay()
        overlay.set_saving()
        duration = self._duration
        wav_path = self._wav_path

        def process():
            try:
                note_title = save_note(content, duration, title=title)
                debug(f"[voice-notes] Saved (from preview): {note_title}")
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
                    self._last_process_body = None
                    self._note_gen += 1   # invalidate in-flight suggestions
                    self._preview_cache = {}
                    self._last_preview_content = None
                    overlay.transcript = None
                    overlay.wav_path = None
                    overlay.suggested_title_text = None
                    overlay.set_idle()
                    title_e = overlay._js_escape(note_title)
                    overlay._eval_js(f"showToast('✓ Saved — {title_e}', '{title_e}.md')")
                    notify("Voice Notes", "Note saved!", note_title)

                self._schedule_ui(on_success)
            except Exception as e:
                import traceback
                debug(f"[voice-notes] Save error: {traceback.format_exc()}")

                def on_error():
                    self.state = AppState.PREVIEW
                    self._set_icon(ICON_IDLE)
                    overlay.push_error("Save failed", str(e))

                self._schedule_ui(on_error)

        threading.Thread(target=process, daemon=True).start()

    def copy_preview_from_overlay(self, body):
        # Prefer whatever is actually on the preview screen; the cache
        # lookup is only a fallback (the button may send a stale payload).
        content = getattr(self, "_last_preview_content", None) \
            or self._preview_cache.get(self._preview_key(body))
        if content:
            copy_to_clipboard(content)
            self._ensure_overlay()._eval_js("showToast('✓ Copied to clipboard')")
        else:
            self._ensure_overlay()._eval_js("showToast('Nothing to copy yet')")

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
        if self.state == AppState.SAVING:
            debug("[voice-notes] Save request ignored — already saving")
            return
        title = body.get("title", "").strip() or None
        if not title:
            title = self._ensure_overlay().suggested_title_text
        action_type = body.get("actionType", "clean_format")
        project = body.get("project", "") or None
        custom_prompt = body.get("customPrompt", "") or None
        copy_clip = body.get("copyClipboard", False)

        if edited_transcript and edited_transcript.strip():
            self._transcript = edited_transcript.strip()

        if not self._transcript:
            # e.g. a save message racing a discard — nothing to save
            debug("[voice-notes] Save ignored — no transcript")
            self.state = AppState.IDLE
            self._ensure_overlay().set_idle()
            return

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
                    self._last_process_body = None
                    self._note_gen += 1   # invalidate in-flight suggestions
                    self._preview_cache = {}
                    self._last_preview_content = None
                    overlay = self._ensure_overlay()
                    overlay.transcript = None
                    overlay.wav_path = None
                    overlay.suggested_title_text = None
                    overlay.set_idle()
                    # In-panel toast (click opens the note); notification as backup
                    title_e = overlay._js_escape(note_title)
                    overlay._eval_js(
                        f"showToast('✓ Saved — {title_e}', '{title_e}.md')"
                    )
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
        if self.state == AppState.SAVING:
            # A save worker is mid-flight and will complete regardless —
            # honoring the discard would lie to the user about the outcome.
            debug("[voice-notes] Discard ignored during SAVING")
            return
        debug("[voice-notes] Discarding")
        self._note_gen += 1          # drop any in-flight title suggestion
        self._last_process_body = None
        self._cancel_paste_timer()
        if self._wav_path:
            try:
                os.unlink(self._wav_path)
            except OSError:
                pass
        self._wav_path = None
        self._transcript = None
        self._preview_cache = {}
        self._last_preview_content = None
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
        self._cancel_paste_timer()
        self._ensure_overlay().set_compact(False)
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
            "engine": ENGINE,
            "active_model": ACTIVE_MODEL,
            "sample_rate": SAMPLE_RATE,
            "vault_path": str(VAULT),
            "notes_folder": CONFIG.get("notes_folder", ""),
            "version": __version__,
        }
        if self.overlay:
            self.overlay.push_settings(settings)

    def save_settings_from_overlay(self, body):
        """Apply-on-change: persist silently (the overlay shows its own toast).
        No echo back to the webview — it already reflects the new values, and
        an echo would fight rapid successive changes."""
        for key in ("default_mode", "default_action", "copy_to_clipboard"):
            if key in body:
                CONFIG[key] = body[key]
        save_config(CONFIG)

    # --- Retry ---

    def retry_from_overlay(self):
        if self._last_process_body and self._transcript:
            self.process_from_overlay(self._last_process_body)
        elif self._last_save_body and self._transcript:
            self.save_from_overlay(self._last_save_body)
        elif self._wav_path and not self._transcript and not _engine_poisoned:
            # Transcription failed (or was refused) — re-run it on the kept WAV
            debug("[voice-notes] Retry: re-running transcription")
            self._start_transcription(self._wav_path, self._duration)
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


def _sweep_stale_wavs(max_age_hours=24):
    """Delete leftover voice_note_*.wav temp files older than max_age_hours."""
    try:
        cutoff = time.time() - max_age_hours * 3600
        for p in Path(tempfile.gettempdir()).glob("voice_note_*.wav"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    debug(f"[voice-notes] Swept stale temp wav: {p.name}")
            except OSError:
                pass
    except Exception as exc:
        debug(f"[voice-notes] Temp sweep error: {exc}")


def _request_microphone_permission():
    """Trigger the macOS microphone permission dialog if not yet granted.

    On modern macOS there is no + button in Settings → Microphone — apps
    only appear there after they call AVCaptureDevice.requestAccessForMediaType.
    We call that here at startup so the user sees the prompt once and the
    entry appears in System Settings so they can re-enable it later.
    """
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        # 0 = not determined → request; 3 = authorized → nothing to do
        # 1 = restricted, 2 = denied → log but can't prompt again
        debug(f"[voice-notes] Microphone auth status: {status}")
        if status == 0:
            debug("[voice-notes] Requesting microphone permission…")
            import threading
            done = threading.Event()
            def handler(granted):
                debug(f"[voice-notes] Microphone permission granted={granted}")
                done.set()
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVMediaTypeAudio, handler
            )
            done.wait(timeout=30)
        elif status == 2:
            debug("[voice-notes] Microphone permission DENIED — user must enable in System Settings")
    except Exception as exc:
        debug(f"[voice-notes] Could not request mic permission: {exc}")


if __name__ == "__main__":
    debug("=== Voice Notes starting ===")
    _sweep_stale_wavs()
    _request_microphone_permission()
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
