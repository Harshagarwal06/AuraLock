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
  - Custom app list — add/remove blocked apps from the menu bar
  - One-click pause/resume from the menu bar

Install dependencies:
    pip3 install rumps

Then run:
    python3 monitor.py
"""

import os
import json
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

# Saved to disk so changes survive restarts
CONFIG_PATH = os.path.expanduser("~/.auralock_config.json")

PRODUCTIVE_APPS = [
    "code", "cursor", "xcode", "terminal", "iterm2", "pycharm",
    "intellij", "eclipse", "sublime text", "vim", "emacs",
    "notion", "obsidian", "word", "pages", "excel", "numbers",
    "zoom", "teams", "slack",
]

DEFAULT_DISTRACTOR_APPS = [
    "discord", "steam", "spotify", "netflix", "youtube",
    "tiktok", "instagram", "twitter", "reddit", "twitch",
    "messages", "facetime", "whatsapp",
]

# ── Config persistence ─────────────────────────────────────────────────

def load_distractor_apps():
    """Load custom distractor list from disk, or use defaults."""
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
            return data.get("distractor_apps", DEFAULT_DISTRACTOR_APPS)
    except Exception:
        return list(DEFAULT_DISTRACTOR_APPS)

def save_distractor_apps(apps):
    """Save current distractor list to disk."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump({"distractor_apps": apps}, f, indent=2)
    except Exception as e:
        print(f"[auralock] Could not save config: {e}")

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

def compute_focus_score(active_app: str, distractor_apps: list) -> float:
    if any(d in active_app for d in distractor_apps):
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

# ── Menu Bar App ───────────────────────────────────────────────────────

class AuraLockApp(rumps.App):
    def __init__(self):
        super().__init__(name="AuraLock", title="🔓 50", quit_button=None)

        self._lock          = threading.Lock()
        self._is_locked     = False
        self._is_paused     = False
        self._in_warning    = False
        self._last_score    = 50.0
        self._last_app      = ""

        # Load saved distractor list
        self._distractor_apps = load_distractor_apps()

        # Pomodoro state
        self._pomo_active   = False
        self._pomo_break    = False
        self._pomo_secs     = 0
        self._pomo_sessions = 0

        # ── Menu items ─────────────────────────────────────────────────
        self.status_item  = rumps.MenuItem("Focus: 50/100 — active")
        self.app_item     = rumps.MenuItem("App: —")

        # Blocked apps section
        self.blocked_header = rumps.MenuItem("🚫 Blocked Apps:")
        self.add_app_item   = rumps.MenuItem("➕  Block current app", callback=self.block_current_app)

        # Build the unblock submenu with items before attaching to menu
        self.remove_menu = rumps.MenuItem("➖  Unblock an app")
        with self._lock:
            apps = list(self._distractor_apps)
        if not apps:
            self.remove_menu.add(rumps.MenuItem("(no apps blocked)"))
        else:
            for app in sorted(apps):
                item = rumps.MenuItem(f"✕  {app.title()}", callback=self._make_unblock_cb(app))
                self.remove_menu.add(item)

        # Pomodoro section
        self.pomo_status  = rumps.MenuItem("Pomodoro: off")
        self.pomo_start   = rumps.MenuItem(f"▶️  Start Focus Session ({POMODORO_WORK_MIN} min)",
                                           callback=self.start_pomodoro)
        self.pomo_stop    = rumps.MenuItem("⏹  Stop Session", callback=self.stop_pomodoro)

        # Controls
        self.pause_item   = rumps.MenuItem("⏸  Pause monitoring", callback=self.toggle_pause)
        self.quit_item    = rumps.MenuItem("Quit AuraLock", callback=self.quit_app)

        self.menu = [
            self.status_item, self.app_item, None,
            self.blocked_header, self.add_app_item, self.remove_menu, None,
            self.pomo_status, self.pomo_start, self.pomo_stop, None,
            self.pause_item, None,
            self.quit_item,
        ]

        # Start background threads
        threading.Thread(target=self._monitor_loop,  daemon=True).start()
        threading.Thread(target=self._pomodoro_loop, daemon=True).start()

    # ── Custom app list ────────────────────────────────────────────────

    def _rebuild_remove_menu(self):
        """Rebuild the 'Unblock an app' submenu from the current list."""
        with self._lock:
            apps = list(self._distractor_apps)
        # Replace the entire menu item instead of clearing it
        new_remove_menu = rumps.MenuItem("➖  Unblock an app")
        if not apps:
            new_remove_menu.add(rumps.MenuItem("(no apps blocked)"))
        else:
            for app in sorted(apps):
                item = rumps.MenuItem(f"✕  {app.title()}", callback=self._make_unblock_cb(app))
                new_remove_menu.add(item)
        # Swap it in the menu
        self.menu["➖  Unblock an app"] = new_remove_menu
        self.remove_menu = new_remove_menu

    def _make_unblock_cb(self, app_name: str):
        """Returns a callback that removes app_name from the blocked list."""
        def cb(sender):
            with self._lock:
                if app_name in self._distractor_apps:
                    self._distractor_apps.remove(app_name)
                    apps_copy = list(self._distractor_apps)
            save_distractor_apps(apps_copy)
            self._rebuild_remove_menu()
            self._update_blocked_header()
            send_notification("AuraLock — App Unblocked",
                              f"{app_name.title()} removed from blocked list.")
            print(f"[auralock] Unblocked: {app_name}")
        return cb

    def block_current_app(self, sender):
        """Block whichever app is currently in the foreground."""
        active = get_active_app()
        if not active or active == "auralock":
            rumps.alert("AuraLock", "No app detected to block. Switch to the app you want to block first, then try again.")
            return

        with self._lock:
            if active in self._distractor_apps:
                send_notification("AuraLock", f"{active.title()} is already blocked!")
                return
            self._distractor_apps.append(active)
            apps_copy = list(self._distractor_apps)

        save_distractor_apps(apps_copy)
        self._rebuild_remove_menu()
        self._update_blocked_header()
        send_notification("AuraLock — App Blocked", f"{active.title()} added to blocked list!")
        print(f"[auralock] Blocked: {active}")

    def _update_blocked_header(self):
        with self._lock:
            count = len(self._distractor_apps)
        self.blocked_header.title = f"🚫 Blocked Apps ({count}):"

    # ── Monitor loop ───────────────────────────────────────────────────

    def _monitor_loop(self):
        while True:
            with self._lock:
                paused      = self._is_paused
                on_break    = self._pomo_break
                pomo_active = self._pomo_active
                distractor_apps = list(self._distractor_apps)

            if not paused and not on_break:
                active_app = get_active_app()
                score      = compute_focus_score(active_app, distractor_apps)

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

                if not locked and not in_warning and not pomo_active and score < LOCK_THRESHOLD:
                    with self._lock:
                        self._in_warning = True
                    threading.Thread(target=self._warning_countdown, daemon=True).start()

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
            with self._lock:
                is_distractor = any(d in active_now for d in self._distractor_apps)

            if is_productive and not is_distractor:
                with self._lock:
                    self._in_warning = False
                print("[auralock] ✅ Focus recovered — lock cancelled!")
                send_notification("AuraLock - All good", "Focus recovered, lock cancelled.")
                return

        with self._lock:
            still_bad = any(d in self._last_app for d in self._distractor_apps)

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
                    with self._lock:
                        self._pomo_sessions += 1
                        self._pomo_break     = True
                        self._pomo_secs      = POMODORO_BREAK_MIN * 60
                        sessions             = self._pomo_sessions
                        self._is_locked      = False
                    send_command("UNLOCK")
                    send_notification("AuraLock 🍅 Session Complete!",
                                      f"Session {sessions} done! Take a {POMODORO_BREAK_MIN} min break.")
                else:
                    with self._lock:
                        self._pomo_break = False
                        self._pomo_secs  = POMODORO_WORK_MIN * 60
                        self._is_locked  = True
                        sessions         = self._pomo_sessions
                    send_command("LOCK")
                    send_notification("AuraLock ▶️ Break Over",
                                      f"Back to work! Session {sessions + 1} — apps locked again.")
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
            self._is_locked   = False
            sessions          = self._pomo_sessions
        self.pomo_start.title  = f"▶️  Start Focus Session ({POMODORO_WORK_MIN} min)"
        self.pomo_status.title = f"Pomodoro: off — {sessions} session(s) today"
        self.title             = "🔓 50"
        send_command("UNLOCK")
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
    AuraLockApp().run()ig