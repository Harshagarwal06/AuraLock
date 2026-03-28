#!/usr/bin/env python3
"""
AuraLock Menu Bar App (monitor.py)
------------------------------------
A macOS menu bar app that monitors which app you're using and
automatically freezes distractors when your focus drops.

Features:
  - Lives in the macOS menu bar (no terminal needed after launch)
  - Shows live focus score in the menu bar icon
  - Warning notification + 10s countdown before locking
  - One-click pause/resume from the menu bar

Install dependencies:
    pip3 install rumps

Then run:
    python3 monitor.py
"""

import os
import time
import threading
import subprocess
import rumps  # pip3 install rumps

# ── Configuration ──────────────────────────────────────────────────────

PIPE_PATH          = "/tmp/auralock.pipe"
POLL_INTERVAL_SEC  = 3       # how often to check the active app
LOCK_THRESHOLD     = 40      # focus score below this triggers warning
UNLOCK_THRESHOLD   = 60      # focus score above this unlocks
WARNING_COUNTDOWN  = 10      # seconds of warning before actually locking

# Apps that mean you are FOCUSED (score goes up)
PRODUCTIVE_APPS = [
    "code", "cursor", "xcode", "terminal", "iterm2", "pycharm",
    "intellij", "eclipse", "sublime text", "vim", "emacs",
    "notion", "obsidian", "word", "pages", "excel", "numbers",
    "zoom", "teams", "slack",
]

# Apps that mean you are DISTRACTED (score drops)
DISTRACTOR_APPS_PY = [
    "discord", "steam", "spotify", "netflix", "youtube",
    "tiktok", "instagram", "twitter", "reddit", "twitch",
    "messages", "facetime",
]

# ── Focus score helpers ────────────────────────────────────────────────

WINDOW_SIZE = 10
app_history = [5] * WINDOW_SIZE   # start neutral so score begins at 50

def get_active_app() -> str:
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
    if any(d in active_app for d in DISTRACTOR_APPS_PY):
        app_history.append(0)
    elif any(p in active_app for p in PRODUCTIVE_APPS):
        app_history.append(10)
    else:
        app_history.append(5)

    if len(app_history) > WINDOW_SIZE:
        app_history.pop(0)

    return round((sum(app_history) / (WINDOW_SIZE * 10)) * 100, 1)

# ── macOS notification helper ──────────────────────────────────────────

def send_notification(title: str, message: str):
    """
    Send a macOS notification. Tries osascript first (works when
    app has notification permission). Falls back to printing clearly
    in the terminal so the countdown is always visible.
    """
    print(f"\n🔔  {title}: {message}")
    script = f'display notification "{message}" with title "{title}" sound name "Funk"'
    subprocess.run(["osascript", "-e", script], capture_output=True)

# ── Daemon pipe helper ─────────────────────────────────────────────────

def send_command(cmd: str):
    try:
        fd = os.open(PIPE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, f"{cmd}\n".encode())
        os.close(fd)
    except OSError:
        pass  # daemon not running — silently skip

# ── Menu Bar App ───────────────────────────────────────────────────────

class AuraLockApp(rumps.App):
    def __init__(self):
        super().__init__(
            name="AuraLock",
            title="🔓 100",   # shown in menu bar
            quit_button=None  # we handle quit ourselves
        )

        # State
        self.is_locked    = False
        self.is_paused    = False
        self.in_warning   = False   # True during the 10s countdown
        self.last_score   = 50.0

        # Menu items
        self.status_item  = rumps.MenuItem("Focus: 50/100 — active")
        self.app_item     = rumps.MenuItem("App: —")
        self.pause_item   = rumps.MenuItem("⏸  Pause monitoring", callback=self.toggle_pause)
        self.quit_item    = rumps.MenuItem("Quit AuraLock", callback=self.quit_app)

        self.menu = [
            self.status_item,
            self.app_item,
            None,              # separator
            self.pause_item,
            None,
            self.quit_item,
        ]

        # Start the background monitoring thread
        t = threading.Thread(target=self.monitor_loop, daemon=True)
        t.start()

    # ── Background monitoring loop ─────────────────────────────────────

    def monitor_loop(self):
        while True:
            if not self.is_paused:
                active_app = get_active_app()
                score      = get_focus_score(active_app)
                self.last_score = score

                # ── Only update the title when NOT in countdown ──
                # During countdown, warning_countdown() owns the title
                # and ticks it down every second. If we overwrite it here
                # every 3s the countdown never visibly changes.
                if not self.in_warning:
                    icon = "🔒" if self.is_locked else "🔓"
                    self.title = f"{icon} {int(score)}"

                # Always update the dropdown menu items
                status_str = "LOCKED 🔒" if self.is_locked else ("WARNING ⚠️" if self.in_warning else "active ✅")
                self.status_item.title = f"Focus: {score}/100 — {status_str}"
                self.app_item.title    = f"App: {active_app or '—'}"

                # State machine
                if not self.is_locked and not self.in_warning and score < LOCK_THRESHOLD:
                    self.in_warning = True
                    threading.Thread(target=self.warning_countdown, daemon=True).start()

                elif self.is_locked and score > UNLOCK_THRESHOLD:
                    self.is_locked  = False
                    self.in_warning = False
                    send_command("UNLOCK")
                    send_notification("AuraLock — Unlocked", "Focus recovered. Apps are back!")

            time.sleep(POLL_INTERVAL_SEC)

    # ── Warning countdown ──────────────────────────────────────────────

    def warning_countdown(self):
        send_notification(
            "AuraLock - Distraction Detected",
            f"Switch back to work or apps will be locked in {WARNING_COUNTDOWN}s..."
        )

        for remaining in range(WARNING_COUNTDOWN, 0, -1):
            # This thread owns the title during countdown — monitor_loop won't touch it
            self.title = f"⚠️ {remaining}s"
            print(f"[auralock] ⚠️  Locking in {remaining}s… (switch app to cancel)")
            time.sleep(1)

            # If focus recovered during countdown — cancel
            if self.last_score > UNLOCK_THRESHOLD:
                self.in_warning = False
                print("[auralock] ✅ Focus recovered — lock cancelled!")
                send_notification("AuraLock - All good", "Focus recovered, lock cancelled.")
                return

        # Countdown finished and still distracted — LOCK
        if self.last_score <= UNLOCK_THRESHOLD:
            self.is_locked  = True
            self.in_warning = False
            send_command("LOCK")
            print("[auralock] 🔒 LOCKED — switch to a productive app to unlock.")
            send_notification(
                "AuraLock - Locked",
                "Distractor apps frozen. Get back to work!"
            )
        else:
            self.in_warning = False

    # ── Menu callbacks ─────────────────────────────────────────────────

    def toggle_pause(self, sender):
        self.is_paused = not self.is_paused
        if self.is_paused:
            sender.title = "▶️  Resume monitoring"
            self.title   = "⏸  —"
            if self.is_locked:
                send_command("UNLOCK")
                self.is_locked = False
            send_notification("AuraLock Paused ⏸", "Monitoring paused. Enjoy your break!")
        else:
            sender.title = "⏸  Pause monitoring"
            self.title   = "🔓 50"
            send_notification("AuraLock Resumed ▶️", "Back to monitoring your focus.")

    def quit_app(self, sender):
        send_command("QUIT")
        rumps.quit_application()


# ── Entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    AuraLockApp().run()