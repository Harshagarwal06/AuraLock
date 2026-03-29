/*
 * AuraLock Daemon (daemon.c)
 * --------------------------
 * A low-level C daemon that listens on a POSIX Named Pipe (FIFO) for
 * commands from the Python focus monitor and issues SIGSTOP / SIGCONT
 * to distractor applications via POSIX kill() system calls.
 *
 * IPC: Named Pipe at /tmp/auralock.pipe
 * Commands: "LOCK\n" | "UNLOCK\n" | "QUIT\n"
 *
 * CMPSC 311 Concepts Demonstrated:
 *   - Process management (fork, exec, pgrep)
 *   - POSIX signal handling (SIGSTOP, SIGCONT)
 *   - Inter-Process Communication via Named Pipes (FIFO)
 *   - File I/O and low-level system calls
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <errno.h>

/* ── Configuration ─────────────────────────────────────────────────── */

#define PIPE_PATH      "/tmp/auralock.pipe"
#define CMD_BUF_SIZE   64

/* Add or remove app names here. These must match the process name
   visible to pgrep (i.e., what you'd see in `ps aux`).             */
/* Exact binary names as they appear in the last part of the path
   in `ps aux`. pgrep -f matches against the full command line.    */
static const char *DISTRACTOR_APPS[] = {
    "MacOS/Discord",
    "MacOS/Spotify",
    "MacOS/WhatsApp",
    "MacOS/steam_osx",
    NULL   /* sentinel – do not remove */
};

/* ── Helpers ────────────────────────────────────────────────────────── */

/*
 * get_pids_for_app()
 * Uses popen() + pgrep to find all PIDs matching `app_name`.
 * Fills `pids` array (max `max_pids` entries). Returns count found.
 */
static int get_pids_for_app(const char *app_name, pid_t *pids, int max_pids) {
    char cmd[256];
    /* Use -f to match against the full command line — this handles
       multi-word app names like "Google Chrome" which pgrep -i fails
       to find when names contain spaces.                             */
    snprintf(cmd, sizeof(cmd), "pgrep -f \"%s\" 2>/dev/null", app_name);

    FILE *fp = popen(cmd, "r");
    if (!fp) return 0;

    int count = 0;
    while (count < max_pids) {
        pid_t pid;
        if (fscanf(fp, "%d", &pid) != 1) break;
        pids[count++] = pid;
    }

    pclose(fp);
    return count;
}

/*
 * signal_app()
 * Sends `sig` (SIGSTOP or SIGCONT) to every running PID of `app_name`.
 */
static void signal_app(const char *app_name, int sig) {
    pid_t pids[64];
    int count = get_pids_for_app(app_name, pids, 64);

    if (count == 0) {
        printf("[daemon] '%s' not running – skipped.\n", app_name);
        return;
    }

    for (int i = 0; i < count; i++) {
        if (kill(pids[i], sig) == 0) {
            printf("[daemon] Sent %s to '%s' (PID %d)\n",
                   sig == SIGSTOP ? "SIGSTOP" : "SIGCONT",
                   app_name, pids[i]);
        } else {
            fprintf(stderr, "[daemon] kill(%d, %d) failed: %s\n",
                    pids[i], sig, strerror(errno));
        }
    }
}

/*
 * lock_all() / unlock_all()
 * Iterate over every configured distractor and stop/continue it.
 */
static void lock_all(void) {
    printf("[daemon] ── LOCKING all distractors ──\n");
    for (int i = 0; DISTRACTOR_APPS[i] != NULL; i++)
        signal_app(DISTRACTOR_APPS[i], SIGSTOP);
}

static void unlock_all(void) {
    printf("[daemon] ── UNLOCKING all distractors ──\n");
    for (int i = 0; DISTRACTOR_APPS[i] != NULL; i++)
        signal_app(DISTRACTOR_APPS[i], SIGCONT);
}

/* ── Main ───────────────────────────────────────────────────────────── */

int main(void) {
    printf("[daemon] AuraLock daemon starting…\n");

    /* Create the Named Pipe (FIFO) if it doesn't already exist */
    if (mkfifo(PIPE_PATH, 0666) == -1 && errno != EEXIST) {
        fprintf(stderr, "[daemon] mkfifo failed: %s\n", strerror(errno));
        return EXIT_FAILURE;
    }

    printf("[daemon] Listening on %s\n", PIPE_PATH);
    printf("[daemon] Blocked apps: ");
    for (int i = 0; DISTRACTOR_APPS[i] != NULL; i++)
        printf("%s%s", DISTRACTOR_APPS[i], DISTRACTOR_APPS[i+1] ? ", " : "\n");

    /* Event loop – reopen the pipe after each command so it blocks
       cleanly waiting for the next writer.                          */
    while (1) {
        /* O_RDONLY blocks here until a writer opens the other end */
        int fd = open(PIPE_PATH, O_RDONLY);
        if (fd == -1) {
            fprintf(stderr, "[daemon] open pipe failed: %s\n", strerror(errno));
            break;
        }

        char buf[CMD_BUF_SIZE];
        memset(buf, 0, sizeof(buf));
        ssize_t n = read(fd, buf, sizeof(buf) - 1);
        close(fd);

        if (n <= 0) continue;

        /* Trim trailing newline */
        buf[strcspn(buf, "\n\r")] = '\0';

        printf("[daemon] Received command: '%s'\n", buf);

        if (strcmp(buf, "LOCK") == 0) {
            lock_all();
        } else if (strcmp(buf, "UNLOCK") == 0) {
            unlock_all();
        } else if (strcmp(buf, "QUIT") == 0) {
            printf("[daemon] QUIT received – cleaning up.\n");
            unlock_all();   /* always unfreeze on exit */
            break;
        } else {
            fprintf(stderr, "[daemon] Unknown command: '%s'\n", buf);
        }
    }

    /* Remove the pipe on clean exit */
    unlink(PIPE_PATH);
    printf("[daemon] Exiting.\n");
    return EXIT_SUCCESS;
}