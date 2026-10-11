#!/usr/bin/env python3
"""Real VTM transport and X11 clipboard in isolated disposable sessions."""
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time

root = Path(__file__).resolve().parents[1]
bin_dir = Path(os.getenv('VTM_TOOLS_BIN', str(root / 'bin')))
vtm = os.getenv('VTM_BINARY', 'vtm')

with tempfile.TemporaryDirectory(prefix='vtm-tools-runtime-') as directory:
    work = Path(directory)
    home = work / 'home'
    home.mkdir()
    runtime = work / 'runtime'
    runtime.mkdir(mode=0o700)
    pin = f'tools-test-{os.getpid()}'
    env = {**os.environ, 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
        'XDG_STATE_HOME': str(home / '.local/state'), 'XDG_RUNTIME_DIR': str(runtime),
        'VTM_DESKTOP_PIN': pin, 'VTM_BINARY': vtm, 'TERM': 'xterm-256color',
        'PATH': str(bin_dir) + ':' + os.environ['PATH'], 'VTM_CONFIG': '', 'VTM': ''}
    env.pop('DISPLAY', None)
    env.pop('WAYLAND_DISPLAY', None)
    server = subprocess.Popen([vtm, '-t', '-s', '-p', pin], stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    server.stdin.write(b'\n'); server.stdin.flush()
    master, slave = pty.openpty()
    fcntl = __import__('fcntl')
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 140, 1120, 640))
    client = clipboard_server = None

    def drain():
        while select.select([master], [], [], .01)[0]:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return
            for query, response in [(b'\x1b[c', b'\x1b[?1;2c'), (b'\x1b[?u', b'\x1b[?0u'), (b'\x1b[6n', b'\x1b[1;1R')]:
                if query in chunk:
                    os.write(master, response)

    def wait(condition, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            drain()
            value = condition()
            if value:
                return value
            time.sleep(.03)
        raise AssertionError('Timed out waiting for runtime operation')

    try:
        wait(lambda: ('@' + pin) in Path('/proc/net/unix').read_text())
        def desktop_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            fd = os.open('/dev/tty', os.O_RDONLY)
            os.dup2(fd, 0)
            os.close(fd)

        client = subprocess.Popen([vtm, '-t', '-p', pin], stdin=slave, stdout=slave, stderr=slave, env=env, preexec_fn=desktop_terminal)
        os.close(slave); slave = -1
        until = time.monotonic() + 1
        while time.monotonic() < until:
            drain(); time.sleep(.03)
        sys = __import__('sys')
        sys.path.insert(0, str(root if (root / 'vtm_tools').is_dir() else Path('/usr/share/vtm-tools')))
        from vtm_tools.common import terminal_geometry
        assert os.readlink(f'/proc/{client.pid}/fd/0') == '/dev/tty'
        assert terminal_geometry(pin) == (40, 140, 1120, 640)
        print('PASS: wallpaper geometry uses the outer desktop PTY with /dev/tty stdin', flush=True)
        result = work / 'result.json'
        args = ['space argument', 'zażółć', '"quoted"', '$(echo no)', 'line\nbreak']
        code = 'import json,os,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(json.dumps([os.getcwd(),sys.argv[2:]],ensure_ascii=False));time.sleep(2)'
        launch = subprocess.Popen([bin_dir / 'vtm-terminal', '--cwd', str(work), '--',
            '/usr/bin/python3', '-c', code, str(result), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        wait(result.exists)
        stdout, stderr = launch.communicate(timeout=10)
        assert launch.returncode == 0, stderr.decode()
        assert json.loads(result.read_text()) == [str(work), args]
        print('PASS: real VTM monitor opens a window with exact arguments and current directory', flush=True)
        response = subprocess.check_output([bin_dir / 'vtm-lua', 'return 37 + 5;'], env=env, timeout=15)
        assert b' lua: ' in response and b'compilation failed' not in response, response
        print('PASS: Lua monitor response received from the existing VTM session', flush=True)
        if __import__('shutil').which('Xvfb') and __import__('shutil').which('xclip'):
            exports = runtime / 'clipboard.env'
            clipboard_server = subprocess.Popen([bin_dir / 'vtm-clipboard-server', '--write-env', exports], env=env, stdout=subprocess.DEVNULL)
            wait(exports.exists)
            import shlex
            for line in exports.read_text().splitlines():
                key, value = line.removeprefix('export ').split('=', 1)
                env[key] = shlex.split(value)[0]
            image = work / 'clipboard.png'
            subprocess.run([bin_dir / 'screenshow', '--backend', 'x11', '--output', image], env=env, check=True, timeout=15)
            assert image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            contents = subprocess.check_output([bin_dir / 'vtm-clipboard', '--paste', '--type', 'image/png'], env=env, timeout=10)
            assert contents == image.read_bytes()
            text = 'zażółć gęślą jaźń\n'
            subprocess.run([bin_dir / 'vtm-clipboard'], input=text.encode(), env=env, check=True, timeout=10)
            assert subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], env=env, timeout=10).decode() == text
            clipboard_server.terminate(); clipboard_server.wait(timeout=5)
            assert not exports.exists()
            assert not list(runtime.glob('vtm-clipboard-*'))
            print('PASS: real screenshot capture, authenticated X11 image clipboard and cleanup', flush=True)
    finally:
        for process in [clipboard_server, client, server]:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
        server.stdin.close()
        if slave >= 0:
            os.close(slave)
        os.close(master)
