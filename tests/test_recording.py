"""Validate the screen-stream/encoder boundary with real FFmpeg processes."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from vtm_tools.capture import record_stream


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is required')
class RecordingTests(unittest.TestCase):
    def test_terminal_ctrl_c_flushes_video_and_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkexec = root / 'pkexec'
            pkexec.write_text('#!/bin/sh\nshift\nexec "$@"\n')
            pkexec.chmod(0o755)
            helper = root / 'capture'
            helper.write_text('#!/bin/sh\nexec ffmpeg -nostdin -hide_banner '
                '-loglevel error -re -f lavfi -i testsrc2=size=64x48:rate=2 '
                '-vf "settb=1/30,setpts=N" -enc_time_base 1:30 -fps_mode passthrough '
                '-pix_fmt bgr0 -c:v rawvideo -threads 1 -f nut pipe:1\n')
            helper.chmod(0o755)
            output = root / 'interrupted.mkv'
            command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-analyzeduration', '1', '-probesize', '32', '-use_wallclock_as_timestamps', '1',
                '-f', 'nut', '-i', 'pipe:0',
                '-re', '-f', 'lavfi', '-i', 'sine=frequency=440',
                '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264',
                '-tune', 'zerolatency', '-c:a', 'aac', str(output)]
            script = ('from pathlib import Path\nimport os\n'
                'from vtm_tools.capture import record_stream\n'
                f'record_stream(Path({str(helper)!r}), "/dev/dri/card0", '
                f'{command!r}, dict(os.environ), Path({str(output)!r}))\n')
            env = {**os.environ, 'PATH': str(root) + ':' + os.environ['PATH']}
            with (root / 'log').open('w+') as log:
                process = subprocess.Popen([sys.executable, '-c', script], env=env,
                    start_new_session=True, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 10
                    while not output.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertTrue(output.exists())
                    time.sleep(2.2)
                    # A terminal sends Ctrl+C to the entire foreground group.
                    os.killpg(process.pid, signal.SIGINT)
                    self.assertEqual(process.wait(timeout=12), 0)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                log.seek(0)
                self.assertNotIn('Immediate exit requested', log.read())
            info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error',
                '-show_streams', '-show_format', '-of', 'json', output]))
            self.assertEqual({s['codec_type'] for s in info['streams']}, {'video', 'audio'})
            self.assertGreater(float(info['format']['duration']), 1.5)
            packets = json.loads(subprocess.check_output(['ffprobe', '-v', 'error',
                '-select_streams', 'v', '-show_packets', '-of', 'json', output]))['packets']
            self.assertGreater(float(packets[-1]['pts_time']), 1.5)

    def test_video_stream_and_user_audio_are_muxed_into_user_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Replace only authorization/DRM capture with a deterministic source.
            pkexec = root / 'pkexec'
            pkexec.write_text('#!/bin/sh\nshift\nexec "$@"\n')
            pkexec.chmod(0o755)
            helper = root / 'capture'
            helper.write_text('#!/bin/sh\n'
                'test "$1" = --record && test "$2" = /dev/dri/card0 || exit 2\n'
                'exec ffmpeg -nostdin -hide_banner -loglevel error -f lavfi '
                '-i testsrc2=size=64x48:rate=30 -t 1 -pix_fmt bgr0 '
                '-c:v rawvideo -threads 1 -f nut pipe:1\n')
            helper.chmod(0o755)
            output = root / 'recording.mkv'
            command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-f', 'nut', '-i', 'pipe:0', '-f', 'lavfi', '-i', 'sine=frequency=440',
                '-map', '0:v:0', '-map', '1:a:0', '-af', 'adelay=1000:all=1',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(output)]
            with patch.dict(os.environ, {'PATH': str(root) + ':' + os.environ['PATH']}):
                record_stream(helper, '/dev/dri/card0', command, dict(os.environ), output)
            info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error',
                '-show_streams', '-of', 'json', output]))
            video = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual((video['width'], video['height']), (64, 48))
            self.assertTrue(any(s['codec_type'] == 'audio' for s in info['streams']))
            self.assertEqual(output.stat().st_uid, os.getuid())
