"""Clipboard contents use the standard Wayland or X11 protocols."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from .common import config


def clipboard_env():
    settings = config('screenshot')
    env = dict(os.environ)
    if not env.get('DISPLAY') and settings.get('display'):
        env['DISPLAY'] = str(settings['display'])
    if not env.get('XAUTHORITY') and settings.get('xauthority'):
        env['XAUTHORITY'] = str(Path(settings['xauthority']).expanduser())
    return env


def x11_target(mime):
    # X11 text clients request UTF8_STRING, while Wayland uses MIME names.
    return 'UTF8_STRING' if mime in ('text/plain', 'text/plain;charset=utf-8') else mime


def copy_file(path, mime='text/plain;charset=utf-8'):
    env = clipboard_env()
    if env.get('WAYLAND_DISPLAY') and shutil.which('wl-copy'):
        command = ['wl-copy', '--type', mime]
    elif env.get('DISPLAY') and shutil.which('xclip'):
        command = ['xclip', '-selection', 'clipboard', '-target', x11_target(mime)]
    else:
        raise RuntimeError('No clipboard backend: use wl-copy on Wayland or xclip with DISPLAY on X11/kmscon')
    with Path(path).open('rb') as stream:
        subprocess.run(command, stdin=stream, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--type', default='text/plain;charset=utf-8', help='MIME type, e.g. image/png')
    parser.add_argument('--paste', action='store_true')
    parser.add_argument('file', nargs='?', type=Path, help='Input file; otherwise read stdin')
    args = parser.parse_args()
    env = clipboard_env()
    wayland = env.get('WAYLAND_DISPLAY') and shutil.which('wl-paste' if args.paste else 'wl-copy')
    if wayland:
        command = ['wl-paste' if args.paste else 'wl-copy', '--type', args.type]
        if args.paste:
            command += ['--no-newline']
    elif env.get('DISPLAY') and shutil.which('xclip'):
        command = ['xclip', '-selection', 'clipboard', '-target', x11_target(args.type)]
        if args.paste:
            command += ['-o']
    else:
        parser.error('No clipboard backend: install wl-clipboard or xclip and provide a display')
    if args.paste and args.file:
        parser.error('--paste writes stdout; no input file allowed')
    if args.file:
        with args.file.open('rb') as stream:
            subprocess.run(command, stdin=stream, env=env, check=True)
    else:
        subprocess.run(command, env=env, check=True)
