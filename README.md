# AuraLock 🔒
### A Context-Aware, AI-Powered Cognitive Firewall

AuraLock is a native macOS Menu Bar application that physically suspends distracting applications the moment you lose focus. Rather than relying on simple timers or static blocklists, AuraLock uses **Google Gemini AI** to understand *what* you are doing in real-time, enforcing productivity through kernel-level process freezing.

Built for **HackPSU** · Best Use of Google Gemini API · CMPSC 311 Extra Credit Track

---

## ✨ Features

- **Gemini AI Contextual Firewall:** AuraLock doesn't just block "YouTube." It reads the active window title. If you are watching a "C++ Tutorial", Gemini allows it. If you switch to a "Funny Cat Compilation", Gemini flags it and freezes the app.
- **Native macOS Menu Bar UI:** Completely controls the daemon, tracks focus score, and manages settings without needing a terminal open.
- **Dynamic Custom Blocklists:** Add or remove specific distractor apps directly from the menu bar UI.
- **Strict Pomodoro Mode:** 25-minute work / 5-minute break sessions that automatically enforce locks.
- **Graceful Lockouts:** A 10-second warning countdown allows you to return to work before your apps are physically frozen in memory.

---

## ⚙️ How It Works (The Hybrid Architecture)

To bridge the gap between high-level LLM context analysis and low-level Unix systems programming, AuraLock utilizes a two-part hybrid architecture:

```text
┌─────────────────────────┐         Named Pipe         ┌──────────────────────┐
│  monitor.py (Python)    │ ── "LOCK" / "UNLOCK" ──▶   │  auralock_daemon (C) │
│  macOS Menu Bar App     │      /tmp/auralock.pipe    │  (System Muscle)     │
│                         │                            │                      │
│ 1. Extracts Window Data │                            │ 1. pgrep → PIDs      │
│ 2. Asks Gemini API      │                            │ 2. kill(SIGSTOP)     │
│ 3. Freezes Custom Apps  │                            │ 3. kill(SIGCONT)     │
└─────────────────────────┘                            └──────────────────────┘
