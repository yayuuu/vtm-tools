"""Provide standard X11 clipboard storage in a pure kmscon session."""
import argparse
import os
from pathlib import Path
import secrets
import select
import shlex
import signal
import subprocess
import tempfile
from .common import atomic_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-env', required=True, type=Path, help='Write shell exports for the session launcher')
    args = parser.parse_args()
    runtime = os.getenv('XDG_RUNTIME_DIR')
    if not runtime or not Path(runtime).is_dir():
        parser.error('XDG_RUNTIME_DIR must point to the user runtime directory')
    process = None
    read_fd = write_fd = -1
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True
        if process:
            process.terminate()

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, stop)
    with tempfile.TemporaryDirectory(prefix='vtm-clipboard-', dir=runtime) as work:
        auth = Path(work) / 'Xauthority'
        auth.touch(mode=0o600)
        cookie = secrets.token_hex(16)
        subprocess.run(['xauth', '-f', str(auth), 'add', ':0', '.', cookie], check=True)
        read_fd, write_fd = os.pipe()
        try:
            process = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '32x32x24',
                '-nolisten', 'tcp', '-auth', str(auth)], pass_fds=(write_fd,), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL)
            os.close(write_fd)
            write_fd = -1
            if not select.select([read_fd], [], [], 10)[0]:
                raise RuntimeError('Xvfb did not allocate a display')
            number = os.read(read_fd, 32).decode().strip()
            if stopped or not number.isdecimal() or process.poll() is not None:
                raise RuntimeError('Xvfb exited before clipboard startup')
            display = ':' + number
            subprocess.run(['xauth', '-f', str(auth), 'add', display, '.', cookie], check=True)
            atomic_write(args.write_env, 'export DISPLAY=' + shlex.quote(display) + '\nexport XAUTHORITY=' + shlex.quote(str(auth)) + '\nexport VTM_CLIPBOARD_HEADLESS=1\n')
            args.write_env.chmod(0o600)
            code = process.wait()
            if code and not stopped:
                raise RuntimeError('Xvfb exited unexpectedly')
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for fd in (read_fd, write_fd):
                if fd >= 0:
                    os.close(fd)
            args.write_env.unlink(missing_ok=True)
