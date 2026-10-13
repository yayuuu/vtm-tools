"""Screenshots and KMS recording, with portable output paths."""
import argparse
import datetime
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from .clipboard import copy_file
from .common import config


def user_directory(kind, fallback):
    if shutil.which('xdg-user-dir'):
        value = subprocess.check_output(['xdg-user-dir', kind], text=True).strip()
        if value:
            return Path(value)
    return Path.home() / fallback


def drm_device(requested=None, sys_root=Path('/sys/class/drm')):
    if requested:
        return str(Path(requested).expanduser())
    for entry in sorted(sys_root.glob('card[0-9]*-*')):
        try:
            if (entry / 'status').read_text().strip() == 'connected':
                return '/dev/dri/' + entry.name.split('-')[0]
        except OSError:
            continue
    cards = sorted(Path('/dev/dri').glob('card[0-9]*'))
    if not cards:
        raise RuntimeError('No DRM device; specify --device')
    return str(cards[0])


def kms_input(device):
    return ['-device', device, '-f', 'kmsgrab', '-framerate', '30', '-i', '-']


def screenshot():
    settings = config('screenshot')
    parser = argparse.ArgumentParser(description='Save a screenshot and copy image/png to the clipboard')
    parser.add_argument('--backend', choices=['auto', 'kmsgrab', 'x11', 'grim'], default=settings.get('backend', 'auto'))
    parser.add_argument('--device', default=os.getenv('SCREENSHOW_DEVICE', settings.get('device')))
    parser.add_argument('--directory', type=Path, default=os.getenv('SCREENSHOW_DIR', settings.get('directory')))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--delay', type=float, default=float(os.getenv('SCREENSHOW_DELAY', '0')))
    parser.add_argument('--no-clipboard', action='store_true')
    args = parser.parse_args()
    if args.delay < 0:
        parser.error('--delay must not be negative')
    directory = args.directory.expanduser() if args.directory else user_directory('PICTURES', 'Pictures') / 'Screenshots'
    output = args.output.expanduser().absolute() if args.output else directory / f'screenshot-{datetime.datetime.now():%Y-%m-%d_%H-%M-%S-%f}.png'
    if output.exists():
        parser.error('Output already exists: ' + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    backend = args.backend
    if backend == 'auto':
        backend = 'grim' if os.getenv('WAYLAND_DISPLAY') and shutil.which('grim') else 'x11' if os.getenv('DISPLAY') and not os.getenv('VTM_CLIPBOARD_HEADLESS') else 'kmsgrab'
    if args.delay:
        time.sleep(args.delay)
    if backend == 'grim':
        command = ['grim', str(output)]
    else:
        command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n']
        if backend == 'kmsgrab':
            device = drm_device(args.device)
            helper = Path('/usr/libexec/vtm-desktop-capture')
            if os.geteuid() != 0 and helper.is_file() and shutil.which('pkexec'):
                try:
                    with output.open('xb') as stream:
                        subprocess.run(['pkexec', '--disable-internal-agent', str(helper), device],
                                       stdout=stream, check=True, timeout=30)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    output.unlink(missing_ok=True)
                    raise RuntimeError('KMS capture requires an active local desktop session')
                command = None
            else:
                command += kms_input(device) + ['-vf', 'hwdownload,format=bgr0']
        else:
            if not os.getenv('DISPLAY'):
                parser.error('X11 capture requires DISPLAY')
            command += ['-f', 'x11grab', '-i', os.environ['DISPLAY']]
        if command is not None:
            command += ['-frames:v', '1', '-update', '1', str(output)]
    try:
        if command is not None:
            subprocess.run(command, check=True)
    except subprocess.CalledProcessError:
        output.unlink(missing_ok=True)
        raise RuntimeError('Screen capture failed. KMS capture requires DRM access and root/CAP_SYS_ADMIN; use a configured capture helper on an unprivileged session.')
    print(output, flush=True)
    if not args.no_clipboard:
        try:
            copy_file(output, 'image/png')
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            print('Screenshot saved, clipboard unavailable: ' + str(error), file=sys.stderr)
            raise SystemExit(1)


def record():
    settings = config('recording')
    parser = argparse.ArgumentParser(description='Record the KMS screen and system audio; stop with Ctrl+C')
    parser.add_argument('device', nargs='?')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--directory', type=Path, default=os.getenv('KMSGRAB_DIR', settings.get('directory')))
    parser.add_argument('--audio-delay', type=int, default=int(os.getenv('KMSGRAB_AUDIO_DELAY_MS', settings.get('audio_delay_ms', 1000))), metavar='MS')
    parser.add_argument('--audio-source', help='PulseAudio/PipeWire source; default: default sink monitor')
    parser.add_argument('--no-audio', action='store_true')
    parser.add_argument('--sudo', action='store_true', help='Explicitly elevate only the capture process')
    args = parser.parse_args()
    if args.audio_delay < 0:
        parser.error('--audio-delay must not be negative')
    directory = args.directory.expanduser() if args.directory else user_directory('VIDEOS', 'Videos') / 'vtm-recordings'
    output = args.output.expanduser().absolute() if args.output else directory / f'kmsgrab-{datetime.datetime.now():%Y-%m-%d_%H-%M-%S-%f}.mkv'
    if output.exists():
        parser.error('Output already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    device = drm_device(args.device)
    helper = Path('/usr/libexec/vtm-desktop-capture')
    use_helper = not args.sudo and os.geteuid() != 0 and helper.is_file() and shutil.which('pkexec')
    if use_helper:
        support = subprocess.run([str(helper), '--supports-recording'], capture_output=True, timeout=3)
        if support.returncode or support.stdout.strip() != b'yes':
            raise RuntimeError('Update vtm-desktop to enable unprivileged recording, or use --sudo')
    command = ['ffmpeg', '-hide_banner', '-nostdin', '-n']
    if use_helper:
        command += ['-thread_queue_size', '512', '-analyzeduration', '0',
                    '-probesize', '32', '-f', 'nut', '-i', 'pipe:0']
    else:
        command += kms_input(device)
    env = dict(os.environ)
    if not args.no_audio:
        source = args.audio_source
        if not source:
            sink = subprocess.check_output(['pactl', 'get-default-sink'], text=True).strip()
            source = sink + '.monitor'
        command += ['-thread_queue_size', '512', '-f', 'pulse', '-i', source,
                    '-map', '0:v:0', '-map', '1:a:0', '-af', f'adelay={args.audio_delay}:all=1']
        if not env.get('PULSE_SERVER'):
            info = subprocess.check_output(['pactl', 'info'], env={**env, 'LC_ALL': 'C'}, text=True)
            env['PULSE_SERVER'] = next((line.removeprefix('Server String: ') for line in info.splitlines() if line.startswith('Server String: ')), '')
        cookie = Path.home() / '.config/pulse/cookie'
        if not env.get('PULSE_COOKIE') and cookie.exists():
            env['PULSE_COOKIE'] = str(cookie)
    command += ['-vf', 'format=bgr0' if use_helper else 'hwdownload,format=bgr0', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p']
    if not args.no_audio:
        command += ['-c:a', 'aac', '-b:a', '192k']
    command += [str(output)]
    if args.sudo and os.getuid() != 0:
        command = ['sudo', '--preserve-env=PULSE_SERVER,PULSE_COOKIE,XDG_RUNTIME_DIR', '--'] + command
    print(f'Recording: {output}; audio delay: {args.audio_delay} ms; stop with Ctrl+C', flush=True)
    if use_helper:
        record_stream(helper, device, command, env, output)
        return
    os.execvpe(command[0], command, env)


def record_stream(helper, device, command, env, output):
    capture = subprocess.Popen(['pkexec', '--disable-internal-agent', str(helper), '--record', device],
                               stdout=subprocess.PIPE)
    encoder = None
    interrupted = False
    try:
        # The terminal sends SIGINT to our process group. Forward it exactly
        # once: a second SIGINT makes FFmpeg skip flushing the output file.
        encoder = subprocess.Popen(command, stdin=capture.stdout, env=env,
                                   start_new_session=True)
        capture.stdout.close()  # Encoder owns the only reader; its exit stops capture.
        try:
            result = encoder.wait()
        except KeyboardInterrupt:
            interrupted = True
            if encoder.poll() is None:
                encoder.send_signal(signal.SIGINT)
            try:
                result = encoder.wait(timeout=10)
            except subprocess.TimeoutExpired:
                encoder.kill()
                result = encoder.wait()
        if result and not interrupted:
            if output.exists() and output.stat().st_size == 0:
                output.unlink()
            raise RuntimeError('Recording failed; KMS capture requires an active local desktop session')
    finally:
        if capture.stdout and not capture.stdout.closed:
            capture.stdout.close()
        if encoder and encoder.poll() is None:
            encoder.kill()
            encoder.wait()
        # The privileged writer exits on SIGPIPE after the encoder closes its
        # reader; the unprivileged caller does not need to signal a root process.
        capture.wait(timeout=10)
