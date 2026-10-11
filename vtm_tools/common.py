"""Shared XDG configuration and VTM monitor transport."""
import fcntl
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import tomllib


def config(section):
    result = {}
    directories = os.getenv("XDG_CONFIG_DIRS", "/etc/xdg").split(":")
    directories = list(reversed(directories)) + [os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))]
    for directory in directories:
        path = Path(directory) / "vtm-tools/config.toml"
        if path.is_file():
            with path.open("rb") as stream:
                result.update(tomllib.load(stream).get(section, {}))
    return result


def lua_quote(value):
    return '"' + ''.join("\\%03d" % byte for byte in str(value).encode()) + '"'


def session_pin(value=None):
    return value if value is not None else os.getenv("VTM_DESKTOP_PIN", config("vtm").get("session", ""))


def monitor(script, pin=None, timeout=10):
    command = [os.getenv("VTM_BINARY", config("vtm").get("command", "vtm"))]
    pin = session_pin(pin)
    if pin:
        command += ["-p", pin]
    command += ["-m"]
    master, slave = pty.openpty()
    process = None
    output = bytearray()
    pending = memoryview((script.rstrip() + "\n").encode())
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=slave, stderr=slave,
            start_new_session=True, env={**os.environ, "TERM": "xterm-256color"})
        os.close(slave)
        slave = -1
        os.set_blocking(process.stdin.fileno(), False)
        deadline = time.monotonic() + timeout
        while True:
            reads, writes, _ = select.select([master], [process.stdin.fileno()] if pending else [], [], .05)
            if reads:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b""
                output.extend(chunk)
                if len(output) > 1024 * 1024:
                    del output[:-1024 * 1024]
            if writes:
                try:
                    sent = os.write(process.stdin.fileno(), pending)
                    pending = pending[sent:]
                except BrokenPipeError:
                    raise RuntimeError("The VTM monitor closed before accepting the command")
                if not pending:
                    process.stdin.close()
            if process.poll() is not None:
                if process.returncode or pending:
                    raise RuntimeError(output.decode(errors="replace").strip() or "Cannot connect to VTM")
                text = output.decode(errors="replace")
                if "compilation failed" in text or "No 'vtm." in text or "runtime error" in text:
                    raise RuntimeError(text.strip())
                return text
            if time.monotonic() >= deadline:
                raise RuntimeError("VTM monitor timed out; check --pin / VTM_DESKTOP_PIN")
    finally:
        if slave >= 0:
            os.close(slave)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process and process.stdin and not process.stdin.closed:
            process.stdin.close()
        os.close(master)


def terminal_geometry(pin=None):
    pin = session_pin(pin)
    # A command launched inside a VTM window has the window's dimensions.
    # Prefer the outer client PTY belonging to the same session and user.
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            args = (entry / "cmdline").read_bytes().split(b"\0")
            if Path(os.fsdecode(args[0])).name != "vtm" or b"-m" in args or b"-s" in args or b"-d" in args:
                continue
            if b"-t" not in args and b"--tui" not in args:
                continue
            given = os.fsdecode(args[args.index(b"-p") + 1]) if b"-p" in args else ""
            if given != pin:
                continue
            for number in (0, 1, 2):
                path = entry / "fd" / str(number)
                try:
                    # Opening another process's /dev/tty resolves to OUR
                    # controlling terminal, not that process's terminal.
                    # The desktop client normally has a concrete PTY on stdout.
                    if os.readlink(path) == "/dev/tty":
                        continue
                    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
                    try:
                        geometry = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, bytes(8)))
                    finally:
                        os.close(fd)
                    if geometry[0] > 0 and geometry[1] > 0:
                        return geometry
                except OSError:
                    continue
        except (OSError, IndexError):
            continue
    # Before the desktop client starts, read the inherited kmscon PTY directly.
    # COLUMNS/LINES may still describe the login manager or a shell window.
    for fd in (0, 1):
        try:
            geometry = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, bytes(8)))
            if geometry[0] > 0 and geometry[1] > 0:
                return geometry
        except OSError:
            continue
    size = shutil.get_terminal_size((80, 25))
    return size.lines, size.columns, 0, 0


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
