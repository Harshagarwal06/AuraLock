# AuraLock Makefile

CC      = gcc
CFLAGS  = -Wall -Wextra -std=c11 -O2
TARGET  = auralock_daemon
SRC     = daemon.c

.PHONY: all clean run-daemon run-monitor install-deps

all: $(TARGET)

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) -o $(TARGET) $(SRC)
	@echo "✅  Built $(TARGET)"

clean:
	rm -f $(TARGET)
	rm -f /tmp/auralock.pipe
	@echo "🧹  Cleaned build artifacts"

# Run the daemon in the foreground (open a second terminal for the monitor)
run-daemon: $(TARGET)
	./$(TARGET)

# Run the Python monitor (requires requests: pip install requests)
run-monitor:
	python3 monitor.py

install-deps:
	pip3 install requests
