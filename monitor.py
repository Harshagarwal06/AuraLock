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
  - Gemini AI contextual firewall
  - One-click pause/resume from the menu bar

Install dependencies:
    pip3 install rumps google-genai

Then run:
    python3 monitor.py
"""

import os
import json
import time
import threading
import subprocess
import rumps
from google import genai

# ── Configuration ──────────────────────────────────────────────────────

PIPE_PATH            = "/tmp/auralock.pipe"
POLL_INTERVAL_SEC    = 6
LOCK_THRESHOLD       = 40
UNLOCK_THRESHOLD     = 60
WARNING_COUNTDOWN    = 10
WINDOW_SIZE          = 5   # smaller = faster reaction (5 × 6s = 30s to change score)

POMODORO_WORK_MIN    = 25
POMODORO_BREAK_MIN   = 5

CONFIG_PATH = os.path.expanduser("~/.auralock_config.json")

# ── Gemini AI Setup ────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE")
GEMINI_CACHE   = {}

PRODUCTIVE_APPS = [
    "code", "cursor", "xcode", "terminal", "iterm2", "pycharm",
    "intellij", "eclipse", "sublime text", "vim", "emacs",
    "notion", "obsidian", "word", "pages", "excel", "numbers",
    "zoom", "teams", "slack",
]

# FIX 1: youtube added to default distractor list
DEFAULT_DISTRACTOR_APPS = [
    "discord", "steam", "spotify", "netflix", "youtube",
    "tiktok", "instagram", "twitter", "reddit", "twitch",
    "messages", "facetime", "whatsapp",
]

# ── Config persistence ─────────────────────────────────────────────────

def load_distractor_apps():
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
            return data.get("distractor_apps", list(DEFAULT_DISTRACTOR_APPS))
    except Exception:
        return list(DEFAULT_DISTRACTOR_APPS)

def save_distractor_apps(apps):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump({"distractor_apps": apps}, f, indent=2)
    except Exception as e:
        print(f"[auralock] Could not save config: {e}")

# ── FIX 2: Get Chrome tab title properly ──────────────────────────────

def get_active_window_details():
    """Returns (app_name, window_title) for the frontmost app."""
    script = '''
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
        set windowTitle to ""
        try
            set windowTitle to name of front window of application process frontApp
        on error
        end try
        return frontApp & "|||" & windowTitle
    end tell
    '''
    try:
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True, timeout=3)
        parts    = result.stdout.strip().split("|||")
        app_name = parts[0].lower().strip()
        window_title = parts[1].strip() if len(parts) > 1 else ""

        # Special handling for Chrome — get the actual tab title
        if "chrome" in app_name:
            chrome_script = 'tell application "Google Chrome" to get title of active tab of front window'
            try:
                r = subprocess.run(["osascript", "-e", chrome_script],
                                   capture_output=True, text=True, timeout=3)
                if r.stdout.strip():
                    window_title = r.stdout.strip()
            except Exception:
                pass

        return app_name, window_title
    except Exception:
        return "", ""

def get_active_app() -> str:
    app, _ = get_active_window_details()
    return app

# ── Gemini AI ──────────────────────────────────────────────────────────

def ask_gemini_is_productive(app_name: str, window_title: str):
    if not window_title:
        return None

    cache_key = f"{app_name}::{window_title}"
    if cache_key in GEMINI_CACHE:
        cached = GEMINI_CACHE[cache_key]
        print(f"[Gemini] Cache: '{window_title[:40]}' → {'✅' if cached else '❌'}")
        return cached

    if GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        return None

    prompt = (
        f"App: '{app_name}'. Window title: '{window_title}'. "
        "Is this productive or a distraction? "
        "Reply NO for: YouTube entertainment, music videos, vlogs, gaming, "
        "memes, Netflix, social media, sports highlights, funny videos. "
        "Reply YES for: coding tutorials, documentation, work, studying, research, news. "
        "If title contains 'MrBeast', 'funny', 'gaming', 'vlog' → NO. "
        "If title contains 'tutorial', 'lecture', 'how to', 'programming' → YES. "
        "ONE WORD ONLY: YES or NO."
    )

    try:
        client   = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        answer   = response.text.strip().upper()
        is_productive = "YES" in answer
        GEMINI_CACHE[cache_key] = is_productive
        print(f"[Gemini] '{window_title[:50]}' → {'Productive ✅' if is_productive else 'Distracted ❌'}")
        return is_productive
    except Exception as e:
        print(f"[Gemini] API error: {e} — falling back to lists")
        return None

# ── Notifications ──────────────────────────────────────────────────────

def send_notification(title: str, message: str):
    print(f"\n🔔  {title}: {message}")
    script = f'display notification "{message}" with title "{title}" sound name "Funk"'
    subprocess.run(["osascript", "-e", script], capture_output=True)

# ── Pipe ───────────────────────────────────────────────────────────────

def send_command(cmd: str, custom_apps: list = None):
    try:
        fd = os.open(PIPE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, f"{cmd}\n".encode())
        os.close(fd)
    except OSError:
        pass

    if custom_apps:
        sig = "-STOP" if cmd == "LOCK" else "-CONT"
        for app in custom_apps:
            if app not in DEFAULT_DISTRACTOR_APPS:
                try:
                    result = subprocess.run(["pgrep", "-i", app], capture_output=True, text=True)
                    for pid in result.stdout.strip().split("\n"):
                        if pid.strip():
                            subprocess.run(["kill", sig, pid.strip()])
                except Exception:
                    pass

# ── Menu Bar App ───────────────────────────────────────────────────────

class AuraLockApp(rumps.App):
    def __init__(self):
        super().__init__(name="AuraLock", title="🔓 50", quit_button=None)

        self._lock            = threading.Lock()
        self._is_locked       = False
        self._is_paused       = False
        self._in_warning      = False
        self._last_score      = 50.0
        self._last_app        = ""
        self._last_window     = ""
        # FIX 3: Smaller window = faster score changes
        self._app_history     = [5] * WINDOW_SIZE
        self._gemini_enabled  = GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE"

        self._distractor_apps = load_distractor_apps()

        self._pomo_active     = False
        self._pomo_break      = False
        self._pomo_secs       = 0
        self._pomo_sessions   = 0

        ai_status = "🤖 Gemini AI: ON" if self._gemini_enabled else "🤖 Gemini AI: OFF"
        self.ai_item      = rumps.MenuItem(ai_status)
        self.status_item  = rumps.MenuItem("Focus: 50/100 — active")
        self.app_item     = rumps.MenuItem("App: —")
        self.window_item  = rumps.MenuItem("Window: —")

        self.blocked_header = rumps.MenuItem("🚫 Blocked Apps:")
        self.add_app_item   = rumps.MenuItem("➕  Block current app", callback=self.block_current_app)

        self.remove_menu = rumps.MenuItem("➖  Unblock an app")
        for app in sorted(self._distractor_apps):
            item = rumps.MenuItem(f"✕  {app.title()}", callback=self._make_unblock_cb(app))
            self.remove_menu.add(item)

        self.pomo_status  = rumps.MenuItem("Pomodoro: off")
        self.pomo_start   = rumps.MenuItem(f"▶️  Start Focus Session ({POMODORO_WORK_MIN} min)",
                                           callback=self.start_pomodoro)
        self.pomo_stop    = rumps.MenuItem("⏹  Stop Session", callback=self.stop_pomodoro)
        self.pause_item   = rumps.MenuItem("⏸  Pause monitoring", callback=self.toggle_pause)
        self.quit_item    = rumps.MenuItem("Quit AuraLock", callback=self.quit_app)

        self.menu = [
            self.ai_item, self.status_item, self.app_item, self.window_item, None,
            self.blocked_header, self.add_app_item, self.remove_menu, None,
            self.pomo_status, self.pomo_start, self.pomo_stop, None,
            self.pause_item, None,
            self.quit_item,
        ]

        self._update_blocked_header()
        threading.Thread(target=self._monitor_loop,  daemon=True).start()
        threading.Thread(target=self._pomodoro_loop, daemon=True).start()

    # ── Focus score ────────────────────────────────────────────────────

    def _compute_focus_score(self, active_app: str, window_title: str, distractor_apps: list) -> float:
        # Combine app + window title for fallback matching
        combined_context = f"{active_app} {window_title}".lower()

        ai_verdict = ask_gemini_is_productive(active_app, window_title)

        if ai_verdict is True:
            self._app_history.append(10)
        elif ai_verdict is False:
            self._app_history.append(0)
        else:
            # Smart fallback — YouTube gets special treatment
            EDUCATIONAL_KEYWORDS = [
                "tutorial", "lecture", "course", "learn", "how to", "howto",
                "programming", "coding", "python", "javascript", "c++", "cpp",
                "math", "science", "physics", "chemistry", "biology",
                "explained", "guide", "study", "exam", "university",
                "khan", "homework", "research", "documentary", "mit", "stanford"
            ]
            ENTERTAINMENT_KEYWORDS = [
                "mrbeast", "mr beast", "mr. bean", "mr bean", "funny", "meme",
                "compilation", "prank", "vlog", "reaction", "gaming", "fortnite",
                "minecraft", "comedy", "roast", "drama", "music video", "song",
                "dance", "celebrity", "movie", "film", "trailer", "shorts"
            ]

            is_youtube    = "youtube" in combined_context
            is_educational   = any(k in combined_context for k in EDUCATIONAL_KEYWORDS)
            is_entertainment = any(k in combined_context for k in ENTERTAINMENT_KEYWORDS)

            if is_youtube:
                if is_educational and not is_entertainment:
                    print(f"[fallback] YOUTUBE EDUCATIONAL ✅")
                    self._app_history.append(10)
                elif is_entertainment:
                    print(f"[fallback] YOUTUBE ENTERTAINMENT ❌")
                    self._app_history.append(0)
                else:
                    print(f"[fallback] YOUTUBE UNKNOWN — treating as distraction")
                    self._app_history.append(0)
            elif any(d in combined_context for d in distractor_apps):
                print(f"[fallback] DISTRACTOR: '{combined_context[:60]}'")
                self._app_history.append(0)
            elif any(p in combined_context for p in PRODUCTIVE_APPS):
                print(f"[fallback] PRODUCTIVE: '{combined_context[:60]}'")
                self._app_history.append(10)
            else:
                print(f"[fallback] NEUTRAL: '{combined_context[:60]}'")
                self._app_history.append(5)

        if len(self._app_history) > WINDOW_SIZE:
            self._app_history.pop(0)

        score = round((sum(self._app_history) / (WINDOW_SIZE * 10)) * 100, 1)
        print(f"[score] history={self._app_history} score={score}")
        return score

    # ── Custom app list ────────────────────────────────────────────────

    def _rebuild_remove_menu(self):
        with self._lock:
            apps = list(self._distractor_apps)
        new_remove_menu = rumps.MenuItem("➖  Unblock an app")
        if not apps:
            new_remove_menu.add(rumps.MenuItem("(no apps blocked)"))
        else:
            for app in sorted(apps):
                item = rumps.MenuItem(f"✕  {app.title()}", callback=self._make_unblock_cb(app))
                new_remove_menu.add(item)
        self.menu["➖  Unblock an app"] = new_remove_menu
        self.remove_menu = new_remove_menu

    def _make_unblock_cb(self, app_name: str):
        def cb(sender):
            with self._lock:
                if app_name in self._distractor_apps:
                    self._distractor_apps.remove(app_name)
                apps_copy = list(self._distractor_apps)
            save_distractor_apps(apps_copy)
            self._rebuild_remove_menu()
            self._update_blocked_header()
            try:
                result = subprocess.run(["pgrep", "-i", app_name], capture_output=True, text=True)
                for pid_str in result.stdout.strip().split("\n"):
                    if pid_str.strip():
                        subprocess.run(["kill", "-CONT", pid_str.strip()])
            except Exception:
                pass
            send_notification("AuraLock", f"{app_name.title()} unblocked and unfrozen!")
        return cb

    def block_current_app(self, sender):
        with self._lock:
            active = self._last_app
        if not active or "auralock" in active or "python" in active:
            rumps.alert("AuraLock", "Switch to the app you want to block, wait 6 seconds, then try again.")
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
                active_app, window_title = get_active_window_details()

                with self._lock:
                    score = self._compute_focus_score(active_app, window_title, distractor_apps)
                    self._last_score  = score
                    self._last_app    = active_app
                    self._last_window = window_title
                    locked     = self._is_locked
                    in_warning = self._in_warning

                status_str = "LOCKED 🔒" if locked else ("WARNING ⚠️" if in_warning else "active ✅")
                self.status_item.title = f"Focus: {score}/100 — {status_str}"
                self.app_item.title    = f"App: {active_app or '—'}"
                self.window_item.title = f"Window: {window_title[:40] or '—'}"

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
                    send_command("UNLOCK", distractor_apps)
                    send_notification("AuraLock — Unlocked", "Focus recovered. Apps are back!")

            elif on_break:
                self.status_item.title = "Break time — relax!"
                self.app_item.title    = "Monitoring paused during break"
                self.window_item.title = "Window: —"

            time.sleep(POLL_INTERVAL_SEC)

    # ── FIX 4: Warning countdown uses combined context ─────────────────

    def _warning_countdown(self):
        send_notification(
            "AuraLock - Distraction Detected",
            f"Switch back to work or apps will be locked in {WARNING_COUNTDOWN}s..."
        )

        combined_context = ""
        for remaining in range(WARNING_COUNTDOWN, 0, -1):
            self.title = f"⚠️ {remaining}s"
            print(f"[auralock] ⚠️  Locking in {remaining}s…")
            time.sleep(1)

            # FIX: Use BOTH app name and window title in countdown check
            active_now, window_now = get_active_window_details()
            combined_context = f"{active_now} {window_now}".lower()

            is_productive = any(p in combined_context for p in PRODUCTIVE_APPS)
            with self._lock:
                is_distractor = any(d in combined_context for d in self._distractor_apps)

            if is_productive and not is_distractor:
                with self._lock:
                    self._in_warning = False
                print("[auralock] ✅ Focus recovered — lock cancelled!")
                send_notification("AuraLock - All good", "Focus recovered, lock cancelled.")
                return

        # Final check using combined_context (not stale last_app)
        with self._lock:
            distractor_apps = list(self._distractor_apps)
        still_bad = any(d in combined_context for d in distractor_apps)

        if still_bad:
            with self._lock:
                self._is_locked  = True
                self._in_warning = False
            send_command("LOCK", distractor_apps)
            print("[auralock] 🔒 LOCKED!")
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
                distractor_apps = list(self._distractor_apps)

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
                    send_command("UNLOCK", distractor_apps)
                    send_notification("AuraLock 🍅 Session Complete!",
                                      f"Session {sessions} done! Take a {POMODORO_BREAK_MIN} min break.")
                else:
                    with self._lock:
                        self._pomo_break = False
                        self._pomo_secs  = POMODORO_WORK_MIN * 60
                        self._is_locked  = True
                        sessions         = self._pomo_sessions
                    send_command("LOCK", distractor_apps)
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
            distractor_apps     = list(self._distractor_apps)
        self.pomo_start.title = f"▶️  Session running ({POMODORO_WORK_MIN} min)"
        send_command("LOCK", distractor_apps)
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
            distractor_apps   = list(self._distractor_apps)
        self.pomo_start.title  = f"▶️  Start Focus Session ({POMODORO_WORK_MIN} min)"
        self.pomo_status.title = f"Pomodoro: off — {sessions} session(s) today"
        self.title             = "🔓 50"
        send_command("UNLOCK", distractor_apps)
        send_notification("AuraLock — Session Stopped",
                          f"Stopped after {sessions} session(s). Apps unlocked!")

    # ── Pause / Quit ───────────────────────────────────────────────────

    def toggle_pause(self, sender):
        with self._lock:
            self._is_paused = not self._is_paused
            paused = self._is_paused
            distractor_apps = list(self._distractor_apps)
            if paused and self._is_locked:
                self._is_locked = False
                send_command("UNLOCK", distractor_apps)
        if paused:
            sender.title = "▶️  Resume monitoring"
            self.title   = "⏸  —"
            send_notification("AuraLock Paused", "Monitoring paused.")
        else:
            sender.title = "⏸  Pause monitoring"
            self.title   = "🔓 50"
            send_notification("AuraLock Resumed", "Back to monitoring your focus.")

    def quit_app(self, sender):
        with self._lock:
            distractor_apps = list(self._distractor_apps)
        send_command("QUIT", distractor_apps)
        rumps.quit_application()


if __name__ == "__main__":
    AuraLockApp().run()