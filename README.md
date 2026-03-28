# AuraLock 🔒
### A Biometric Cognitive Firewall

AuraLock is a real-time, biometrically-driven system daemon that physically suspends distracting applications the moment your focus slips — and automatically restores them when you're back in the zone.

Built for **HackPSU** · CMPSC 311 Extra Credit Track · Best Use of Presage

---

## How It Works

```
┌─────────────────────┐        Named Pipe         ┌──────────────────────┐
│   monitor.py        │  ── "LOCK" / "UNLOCK" ──▶  │   auralock_daemon    │
│   (Python)          │       /tmp/auralock.pipe    │   (C)                │
│                     │                             │                      │
│  Presage API        │                             │  pgrep → PIDs        │
│  → focus score      │                             │  kill(SIGSTOP)       │
│  → threshold check  │                             │  kill(SIGCONT)       │
└─────────────────────┘                             └──────────────────────┘
```

1. **monitor.py** polls the Presage API every 3 seconds for the user's real-time focus score.
2. When focus drops below the **LOCK threshold (40)**, it writes `"LOCK"` to a POSIX Named Pipe.
3. The **C daemon** reads the command, uses `pgrep` to find all running PIDs for each distractor app, and sends `SIGSTOP` via `kill()` — freezing them at the OS level.
4. When focus recovers above the **UNLOCK threshold (60)**, `"UNLOCK"` is sent and `SIGCONT` thaws the apps exactly as they were.

---

## Architecture

### The Brain — `monitor.py` (Python)
- Interfaces with the Presage SDK/API to fetch live biometric focus data
- Implements hysteresis (separate lock/unlock thresholds) to prevent oscillation
- Communicates with the daemon via IPC Named Pipe
- Runs in simulation mode (sine-wave score) when no API key is configured — great for testing

### The Muscle — `daemon.c` (C)
- POSIX Named Pipe server — blocks on `open()` waiting for commands
- Maps application names → live PIDs via `popen("pgrep ...")`
- Issues `SIGSTOP` / `SIGCONT` to every matching PID via `kill()`
- Gracefully cleans up: sends `SIGCONT` to all apps on `QUIT` or `Ctrl-C`

### The Bridge — IPC via Named Pipe (`/tmp/auralock.pipe`)
- Created by the daemon with `mkfifo()`
- Simple line-based protocol: `LOCK\n` | `UNLOCK\n` | `QUIT\n`
- Non-blocking write from Python; blocking read in C keeps CPU usage near zero

---

## CMPSC 311 Criteria

| Requirement | How AuraLock satisfies it |
|---|---|
| **Written in C** | `daemon.c` — the entire OS-interaction layer is pure C |
| **Systems-related** | Process management, POSIX signals, IPC/FIFOs, File I/O |
| **Provides an abstraction** | The Python layer (or a human) sends only `"LOCK"`. All PID discovery, signal routing, and process scheduling complexity is hidden inside the daemon |

---

## Quickstart

### Prerequisites
- macOS (uses `pgrep` and POSIX signals — Linux works too)
- GCC or Clang
- Python 3.8+

### 1. Build the daemon
```bash
make
```

### 2. Install Python dependencies
```bash
make install-deps
# or: pip3 install requests
```

### 3. Configure your Presage credentials
```bash
export PRESAGE_API_KEY="your_key_here"
export PRESAGE_USER_ID="your_user_id_here"
```
> Without credentials the monitor runs in **simulation mode** (sine-wave score) — useful for testing the full lock/unlock cycle without a live API connection.

### 4. Run (two terminals)

**Terminal 1 — start the daemon:**
```bash
make run-daemon
# or: ./auralock_daemon
```

**Terminal 2 — start the monitor:**
```bash
make run-monitor
# or: python3 monitor.py
```

Press `Ctrl-C` in the monitor window to gracefully shut down both processes.

---

## Configuration

| Variable | Location | Default | Description |
|---|---|---|---|
| `DISTRACTOR_APPS[]` | `daemon.c` | Discord, Chrome, Steam, Spotify | Apps to freeze |
| `LOCK_THRESHOLD` | `monitor.py` | 40 | Score below this triggers LOCK |
| `UNLOCK_THRESHOLD` | `monitor.py` | 60 | Score above this triggers UNLOCK |
| `POLL_INTERVAL_SEC` | `monitor.py` | 3 | Seconds between API calls |
| `PRESAGE_API_KEY` | env var | — | Your Presage API key |
| `PRESAGE_USER_ID` | env var | — | Presage session/user ID |

---

## Project Structure

```
auralock/
├── daemon.c       # C daemon — process management & signal handling
├── monitor.py     # Python monitor — Presage API + IPC bridge
├── Makefile       # Build system
└── README.md      # This file
```

---

## Security Notes

- The daemon only accepts commands from processes on the **same machine** via a local FIFO — there is no network exposure.
- `SIGSTOP` cannot be caught or ignored by the target process; it is enforced by the kernel.
- On exit, the daemon always sends `SIGCONT` to every tracked app — apps are never left frozen.

---

## Team
Built at HackPSU with ❤️ and caffeine.
