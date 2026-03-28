#!/usr/bin/env python3
"""
AuraLock Focus Monitor (monitor.py)
------------------------------------
Polls the Presage API for the user's real-time focus/engagement score.
When focus drops below LOCK_THRESHOLD, it writes "LOCK" to the daemon's
Named Pipe. When it recovers above UNLOCK_THRESHOLD, it writes "UNLOCK".

Usage:
    python3 monitor.py

Environment variables (or edit the CONFIG block below):
    PRESAGE_API_KEY   – Your Presage API key
    PRESAGE_USER_ID   – The user ID / session token for the current subject
"""

import os
import time
import signal
import subprocess

# ── Configuration ──────────────────────────────────────────────────────

PIPE_PATH = "/tmp/auralock.pipe"

POLL_INTERVAL_SEC  = 3      # how often to check focus (seconds)
LOCK_THRESHOLD     = 40     # focus score below this → LOCK
UNLOCK_THRESHOLD   = 60     # focus score above this → UNLOCK

# Apps that mean you are FOCUSED (score stays high)
PRODUCTIVE_APPS = [
    "code", "cursor", "xcode", "terminal", "iterm2", "pycharm",
    "intellij", "eclipse", "sublime text", "vim", "emacs",
    "notion", "obsidian", "word", "pages", "excel", "numbers",
    "zoom", "teams", "slack",  # meetings = productive
]

# Apps that mean you are DISTRACTED (score drops)
DISTRACTOR_APPS_PY = [
    "discord", "steam", "spotify", "netflix", "youtube",
    "tiktok", "instagram", "twitter", "reddit", "twitch",
    "messages", "facetime",
]

# ── State ──────────────────────────────────────────────────────────────

is_locked = False
running   = True

# ── Signal handling (Ctrl-C sends QUIT to daemon) ──────────────────────

def handle_sigint(sig, frame):
    global running
    print("\n[monitor] Interrupted – sending QUIT to daemon…")
    send_command("QUIT")
    running = False

signal.signal(signal.SIGINT, handle_sigint)

# ── Pipe helper ────────────────────────────────────────────────────────

def send_command(cmd: str):
    """Write a command string to the AuraLock daemon Named Pipe."""
    try:
        # Open in non-blocking write mode so we don't hang if daemon is gone
        fd = os.open(PIPE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, f"{cmd}\n".encode())
        os.close(fd)
        print(f"[monitor] → Sent '{cmd}' to daemon")
    except OSError as e:
        print(f"[monitor] Could not write to pipe: {e}")
        print("[monitor] Is the daemon running? Start it with: ./auralock_daemon")

# ── App-usage focus tracking ───────────────────────────────────────────

# Rolling window: last N active app checks
WINDOW_SIZE = 10

# Pre-fill with neutral (5) so score starts at 50, not 0
# This prevents a false LOCK the moment the program launches
app_history = [5] * WINDOW_SIZE

def get_active_app() -> str:
    """Returns the name of the currently focused app on macOS (lowercase)."""
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip().lower()
    except Exception:
        return ""

def get_focus_score(active_app: str) -> float:
    """
    Derives a focus score (0-100) from recent app usage history.
    Accepts the already-fetched active_app name so we don't call
    get_active_app() twice per loop.

    - Distractor app in focus  → 0 points for this sample
    - Productive app in focus  → 10 points for this sample
    - Unknown/neutral app      → 5 points for this sample

    Score = (sum of last 10 samples / max possible 100) * 100
    """
    if any(d in active_app for d in DISTRACTOR_APPS_PY):
        app_history.append(0)
    elif any(p in active_app for p in PRODUCTIVE_APPS):
        app_history.append(10)
    else:
        app_history.append(5)

    if len(app_history) > WINDOW_SIZE:
        app_history.pop(0)

    score = (sum(app_history) / (WINDOW_SIZE * 10)) * 100
    return round(score, 1)

# ── Main loop ──────────────────────────────────────────────────────────

def main():
    global is_locked

    print("[monitor] AuraLock — App-Usage Focus Monitor")
    print(f"[monitor] Lock threshold  : < {LOCK_THRESHOLD}")
    print(f"[monitor] Unlock threshold: > {UNLOCK_THRESHOLD}")
    print(f"[monitor] Poll interval   : {POLL_INTERVAL_SEC}s")
    print(f"[monitor] Pipe            : {PIPE_PATH}\n")
    print("[monitor] Monitoring focus… (Ctrl-C to stop)\n")

    while running:
        active_app = get_active_app()       # one call, used everywhere
        score = get_focus_score(active_app)

        bar_len = int(score / 2)
        bar     = "█" * bar_len + "░" * (50 - bar_len)
        status  = "LOCKED 🔒" if is_locked else "active ✅"
        print(f"[monitor] App: {active_app:<25} Focus: {score:5.1f}/100  [{bar}]  {status}")

        if not is_locked and score < LOCK_THRESHOLD:
            print(f"[monitor] Focus dropped below {LOCK_THRESHOLD} – locking!")
            send_command("LOCK")
            is_locked = True
        elif is_locked and score > UNLOCK_THRESHOLD:
            print(f"[monitor] Focus recovered above {UNLOCK_THRESHOLD} – unlocking!")
            send_command("UNLOCK")
            is_locked = False

        time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    main()