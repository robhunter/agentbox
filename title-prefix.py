#!/usr/bin/env python3
"""Run a command in a pseudo-terminal, prefixing every terminal title it sets.

Usage: title-prefix PREFIX COMMAND [ARGS...]

Makes several agent sessions distinguishable in the host terminal's tab list.
"""

import fcntl
import os
import pty
import re
import select
import signal
import sys
import termios
import tty

# OSC 0/1/2 set the icon name and/or window title; either BEL or ST terminates them
TITLE_SEQUENCE = re.compile(rb"\x1b\]([012]);([^\x07\x1b]*)(\x07|\x1b\\)")
MAX_HELD_BYTES = 4096


class TitlePrefixer:
    def __init__(self, prefix):
        self.prefix = prefix.encode()
        self.held = b""

    def _prefix_title(self, match):
        code, title, terminator = match.groups()
        if title and not title.startswith(self.prefix):
            title = self.prefix + b" - " + title
        return b"\x1b]" + code + b";" + title + terminator

    def feed(self, data):
        """Returns the bytes that can be written now; holds back a trailing incomplete sequence."""
        data = TITLE_SEQUENCE.sub(self._prefix_title, self.held + data)
        start = data.rfind(b"\x1b]")
        if start != -1 and not self._is_terminated(data[start:]) and len(data) - start < MAX_HELD_BYTES:
            self.held = data[start:]
        elif data.endswith(b"\x1b"):
            start = len(data) - 1
            self.held = data[start:]
        else:
            start = len(data)
            self.held = b""
        return data[:start]

    @staticmethod
    def _is_terminated(sequence):
        return b"\x07" in sequence or b"\x1b\\" in sequence


def copy_window_size(source_fd, target_fd):
    size = fcntl.ioctl(source_fd, termios.TIOCGWINSZ, b"\0" * 8)
    fcntl.ioctl(target_fd, termios.TIOCSWINSZ, size)


def write_all(fd, data):
    while data:
        data = data[os.write(fd, data):]


def relay(child_fd, prefixer):
    stdin_fd, stdout_fd = sys.stdin.fileno(), sys.stdout.fileno()
    inputs = [child_fd, stdin_fd]
    while True:
        readable, _, _ = select.select(inputs, [], [])
        if child_fd in readable:
            try:
                data = os.read(child_fd, 65536)
            except OSError:
                data = b""
            if not data:
                write_all(stdout_fd, prefixer.held)
                return
            write_all(stdout_fd, prefixer.feed(data))
        if stdin_fd in readable:
            data = os.read(stdin_fd, 65536)
            if data:
                write_all(child_fd, data)
            else:
                inputs.remove(stdin_fd)


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: title-prefix PREFIX COMMAND [ARGS...]")
    prefix, command = sys.argv[1], sys.argv[2:]

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        os.execvp(command[0], command)

    pid, child_fd = pty.fork()
    if pid == 0:
        os.execvp(command[0], command)

    copy_window_size(sys.stdin.fileno(), child_fd)
    signal.signal(signal.SIGWINCH, lambda *_: copy_window_size(sys.stdin.fileno(), child_fd))
    for forwarded in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(forwarded, lambda signum, _: os.kill(pid, signum))

    saved_mode = termios.tcgetattr(sys.stdin.fileno())
    tty.setraw(sys.stdin.fileno())
    try:
        relay(child_fd, TitlePrefixer(prefix))
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, saved_mode)

    _, status = os.waitpid(pid, 0)
    exit_code = os.waitstatus_to_exitcode(status)
    sys.exit(exit_code if exit_code >= 0 else 128 - exit_code)


if __name__ == "__main__":
    main()
