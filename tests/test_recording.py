"""Validate the screen-stream/encoder boundary with real FFmpeg processes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from vtm_tools.capture import record_stream


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is required')
class RecordingTests(unittest.TestCase):
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
