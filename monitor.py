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
  - Pomodoro focus sessions (25min work / 5min break)
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
import rumps

# ── Configuration ──────────────────────────────────────────────────────

PIPE_PATH            = "/tmp/auralock.pipe"
POLL_INTERVAL_SEC    = 3
LOCK_THRESHOLD       = 40
UNLOCK_THRESHOLD     = 60
WARNING_COUNTDOWN    = 10

POMODORO_WORK_MIN    = 25
POMODORO_BREAK_MIN   = 5

PRODUCTIVE_APPS = [
    "code", "cursor", "xcode", "terminal", "iterm2", "pycharm",
    "intellij", "eclipse", "sublime text", "vim", "emacs",
    "notion", "obsidian", "word", "pages", "excel", "numbers",
    "zoom", "teams", "slack",
]

DISTRACTOR_APPS_PY = [
    "discord", "steam", "spotify", "netflix", "youtube",
    "tiktok", "instagram", "twitter", "reddit", "twitch",
    "messages", "facetime", "whatsapp",
]

# ── Focus score ────────────────────────────────────────────────────────

WINDOW_SIZE = 10
app_history = [5] * WINDOW_SIZE

def get_active_app() -> str:
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    try:
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True, timeout=3)
        return result.stdout.strip().lower()
    except Exception:
        return ""

def compute_focus_score(active_app: str) -> float:
    if any(d in active_app for d in DISTRACTOR_APPS_PY):
        app_history.append(0)
    elif any(p in active_app for p in PRODUCTIVE_APPS):
        app_history.append(10)
    else:
        app_history.append(5)
    if len(app_history) > WINDOW_SIZE:
        app_history.pop(0)
    return round((sum(app_history) / (WINDOW_SIZE * 10)) * 100, 1)

# ── Notifications ──────────────────────────────────────────────────────

def send_notification(title: str, message: str):
    print(f"\n🔔  {title}: {message}")
    script = f'display notification "{message}" with title "{title}" sound name "Funk"'
    subprocess.run(["osascript", "-e", script], capture_output=True)

# ── Pipe ───────────────────────────────────────────────────────────────

def send_command(cmd: str):
    try:
        fd = os.open(PIPE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, f"{cmd}\n".encode())
        os.close(fd)
    except OSError:
        pass

# ── App ────────────────────────────────────────────────────────────────

class AuraLockApp(rumps.App):
    def __init__(self):
        super().__init__(name="AuraLock", title="🔓 50", quit_button=None)

        self._lock          = threading.Lock()
        self._is_locked     = False
        self._is_paused     = False
        self._in_warning    = False
        self._last_score    = 50.0
        self._last_app      = ""

        self._pomo_active   = False
        self._pomo_break    = False
        self._pomo_secs     = 0
        self._pomo_sessions = 0

        self.status_item = rumps.MenuItem("Focus: 50/100 — active")
        self.app_item    = rumps.MenuItem("App: —")
        self.pomo_status = rumps.MenuItem("Pomodoro: off")
        self.pomo_start  = rumps.MenuItem(f"▶️  Start Focus Session ({POMODORO_WORK_MIN} min)",
                                          callback=self.start_pomodoro)
        self.pomo_stop   = rumps.MenuItem("⏹  Stop Session", callback=self.stop_pomodoro)
        self.pause_item  = rumps.MenuItem("⏸  Pause monitoring", callback=self.toggle_pause)
        self.quit_item   = rumps.MenuItem("Quit AuraLock", callback=self.quit_app)

        self.menu = [
            self.status_item, self.app_item, None,
            self.pomo_status, self.pomo_start, self.pomo_stop, None,
            self.pause_item, None,
            self.quit_item,
        ]

        threading.Thread(target=self._monitor_loop,  daemon=True).start()
        threading.Thread(target=self._pomodoro_loop, daemon=True).start()

    # ── Monitor loop ───────────────────────────────────────────────────

    def _monitor_loop(self):
        while True:
            with self._lock:
                paused      = self._is_paused
                on_break    = self._pomo_break
                pomo_active = self._pomo_active

            if not paused and not on_break:
                active_app = get_active_app()
                score      = compute_focus_score(active_app)

                with self._lock:
                    self._last_score = score
                    self._last_app   = active_app
                    locked     = self._is_locked
                    in_warning = self._in_warning

                status_str = "LOCKED 🔒" if locked else ("WARNING ⚠️" if in_warning else "active ✅")
                self.status_item.title = f"Focus: {score}/100 — {status_str}"
                self.app_item.title    = f"App: {active_app or '—'}"

                if not in_warning and not pomo_active:
                    icon = "🔒" if locked else "🔓"
                    self.title = f"{icon} {int(score)}"

                # Trigger warning only when not locked, not in warning, and not in Pomodoro work session
                if not locked and not in_warning and not pomo_active and score < LOCK_THRESHOLD:
                    with self._lock:
                        self._in_warning = True
                    threading.Thread(target=self._warning_countdown, daemon=True).start()

                # FIX: Only auto-unlock if NOT in an active Pomodoro work session
                elif locked and score > UNLOCK_THRESHOLD and not pomo_active:
                    with self._lock:
                        self._is_locked  = False
                        self._in_warning = False
                    send_command("UNLOCK")
                    send_notification("AuraLock — Unlocked", "Focus recovered. Apps are back!")

            elif on_break:
                self.status_item.title = "Break time — relax!"
                self.app_item.title    = "Monitoring paused during break"

            time.sleep(POLL_INTERVAL_SEC)

    # ── Warning countdown ──────────────────────────────────────────────

    def _warning_countdown(self):
        send_notification(
            "AuraLock - Distraction Detected",
            f"Switch back to work or apps will be locked in {WARNING_COUNTDOWN}s..."
        )

        for remaining in range(WARNING_COUNTDOWN, 0, -1):
            self.title = f"⚠️ {remaining}s"
            print(f"[auralock] ⚠️  Locking in {remaining}s… (switch app to cancel)")
            time.sleep(1)

            active_now    = get_active_app()
            is_productive = any(p in active_now for p in PRODUCTIVE_APPS)
            is_distractor = any(d in active_now for d in DISTRACTOR_APPS_PY)

            if is_productive and not is_distractor:
                with self._lock:
                    self._in_warning = False
                print("[auralock] ✅ Focus recovered — lock cancelled!")
                send_notification("AuraLock - All good", "Focus recovered, lock cancelled.")
                return

        # Still distracted after countdown — lock
        with self._lock:
            still_bad = any(d in self._last_app for d in DISTRACTOR_APPS_PY) or \
                        not any(p in self._last_app for p in PRODUCTIVE_APPS)

        if still_bad:
            with self._lock:
                self._is_locked  = True
                self._in_warning = False
            send_command("LOCK")
            print("[auralock] 🔒 LOCKED — switch to a productive app to unlock.")
            send_notification("AuraLock - Locked", "Distractor apps frozen. Get back to work!")
        else:
            with self._lock:
                self._in_warning = False

    # ── Pomodoro loop ──────────────────────────────────────────────────

    def _pomodoro_loop(self):
        while True:
            with self._lock:
                active   = self._pomo_active
                secs     = self._pomo_secs
                on_break = self._pomo_break
                sessions = self._pomo_sessions
                warning  = self._in_warning

            if active and secs > 0:
                mins = secs // 60
                s    = secs % 60

                if not warning:
                    if on_break:
                        self.title = f"☕ {mins:02d}:{s:02d}"
                        self.pomo_status.title = f"Break — {mins:02d}:{s:02d} remaining"
                    else:
                        self.title = f"🍅 {mins:02d}:{s:02d}"
                        self.pomo_status.title = f"Session {sessions + 1} — {mins:02d}:{s:02d} left"

                with self._lock:
                    self._pomo_secs -= 1
                time.sleep(1)

            elif active and secs == 0:
                if not on_break:
                    # Work session done → start break, unlock apps
                    with self._lock:
                        self._pomo_sessions += 1
                        self._pomo_break     = True
                        self._pomo_secs      = POMODORO_BREAK_MIN * 60
                        sessions             = self._pomo_sessions
                        self._is_locked      = False  # unlock during break
                    send_command("UNLOCK")
                    send_notification(
                        "AuraLock 🍅 Session Complete!",
                        f"Session {sessions} done! Take a {POMODORO_BREAK_MIN} min break — apps unlocked!"
                    )
                else:
                    # Break done → start new work session, re-lock apps
                    with self._lock:
                        self._pomo_break = False
                        self._pomo_secs  = POMODORO_WORK_MIN * 60
                        self._is_locked  = True   # FIX: re-lock at start of each work session
                        sessions         = self._pomo_sessions
                    send_command("LOCK")           # FIX: actually freeze apps again
                    send_notification(
                        "AuraLock ▶️ Break Over",
                        f"Back to work! Session {sessions + 1} — apps locked again."
                    )
            else:
                time.sleep(1)

    # ── Pomodoro controls ──────────────────────────────────────────────

    def start_pomodoro(self, sender):
        with self._lock:
            if self._pomo_active:
                return
            self._pomo_active   = True
            self._pomo_break    = False
            self._pomo_secs     = POMODORO_WORK_MIN * 60
            self._pomo_sessions = 0
            self._is_locked     = True
        self.pomo_start.title = f"▶️  Session running ({POMODORO_WORK_MIN} min)"
        send_command("LOCK")
        send_notification("AuraLock 🍅 Focus Session Started",
                          f"Distracting apps locked. Work for {POMODORO_WORK_MIN} min!")

    def stop_pomodoro(self, sender):
        with self._lock:
            if not self._pomo_active:
                return
            self._pomo_active = False
            self._pomo_break  = False
            self._pomo_secs   = 0
            self._is_locked   = False   # FIX: mark as unlocked
            sessions          = self._pomo_sessions
        self.pomo_start.title  = f"▶️  Start Focus Session ({POMODORO_WORK_MIN} min)"
        self.pomo_status.title = f"Pomodoro: off — {sessions} session(s) today"
        self.title             = "🔓 50"
        send_command("UNLOCK")          # FIX: actually unfreeze apps
        send_notification("AuraLock — Session Stopped",
                          f"Stopped after {sessions} session(s). Apps unlocked!")

    # ── Pause / Quit ───────────────────────────────────────────────────

    def toggle_pause(self, sender):
        with self._lock:
            self._is_paused = not self._is_paused
            paused = self._is_paused
            if paused and self._is_locked:
                self._is_locked = False
                send_command("UNLOCK")

        if paused:
            sender.title = "▶️  Resume monitoring"
            self.title   = "⏸  —"
            send_notification("AuraLock Paused", "Monitoring paused.")
        else:
            sender.title = "⏸  Pause monitoring"
            self.title   = "🔓 50"
            send_notification("AuraLock Resumed", "Back to monitoring your focus.")

    def quit_app(self, sender):
        send_command("QUIT")
        rumps.quit_application()


if __name__ == "__main__":
    AuraLockApp().run()