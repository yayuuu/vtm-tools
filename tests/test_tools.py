import base64
import struct
import zlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtm_tools import capture, clipboard, common, panel, runner, wallpaper


class ToolsTests(unittest.TestCase):
    def test_lua_quotes_unicode_and_control_characters(self):
        value = 'a"\\\nż\x1b'
        quoted = common.lua_quote(value)
        self.assertEqual(bytes(int(n) for n in quoted[1:-1].split('\\')[1:]).decode(), value)

    def test_runner_argument_vector_and_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ['space argument', "'quoted'", 'line\nż', '$(touch /should-not-exist)']
            with patch.object(sys, 'argv', [str(Path(__file__).resolve().parents[1] / 'bin/vtm-terminal'), '--cwd', directory, '--', sys.executable, *args]), patch.object(runner, 'monitor') as monitor:
                runner.main()
            script = monitor.call_args.args[0]
            self.assertIn('hidden=', script)
            # Read cmd from the generated Lua to verify the exact payload sent to VTM.
            encoded = script.split('cmd="')[1].split('"')[0]
            command = bytes(int(n) for n in encoded.split('\\')[1:]).decode()
            payload = json.loads(base64.urlsafe_b64decode(command.split()[-1]))
            self.assertEqual(payload['argv'][1:], args)
            self.assertEqual(payload['cwd'], directory)

    def test_runner_executes_no_extra_shell(self):
        root = Path(__file__).resolve().parents[1]
        args = ['a b', '";false;', 'zażółć', '$(echo unsafe)', 'line\nbreak']
        with tempfile.TemporaryDirectory() as directory:
            code = 'import json,os,sys;print(json.dumps([os.getcwd(),sys.argv[1:]],ensure_ascii=False))'
            payload = base64.urlsafe_b64encode(json.dumps({'argv': [sys.executable, '-c', code, *args], 'cwd': directory}).encode()).decode()
            result = subprocess.check_output([root / 'bin/vtm-tools-exec', payload], text=True)
            self.assertEqual(json.loads(result), [directory, args])

    def test_background_replacement_does_not_modify_other_tiles(self):
        text = '<config><desktop><taskbar><tile="application"/></taskbar><background><tile="old"/><other/></background></desktop></config>'
        result = wallpaper.replace_background(text, '<tile="new"/>')
        self.assertIn('<tile="application"/>', result)
        self.assertIn('<background><tile="new"/><other/></background>', result)

    def test_background_can_be_inserted(self):
        for text in ['', '<config></config>', '<config><desktop></desktop></config>']:
            result = wallpaper.replace_background(text, '<tile="new"/>')
            self.assertIn('<background><tile="new"/></background>', result)

    def test_empty_tile_cannot_loop_forever(self):
        with self.assertRaises(ValueError):
            wallpaper.repeat_art('\x1b[0m', '10x5')
        self.assertEqual(wallpaper.repeat_art('ab', '5x2'), 'ababa\x1b[0m\nababa\x1b[0m')

    def test_config_layering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('system', '110:40'), ('user', '90:30')]:
                path = root / name / 'vtm-tools'
                path.mkdir(parents=True)
                (path / 'config.toml').write_text(f'[terminal]\nsize="{value}"\n')
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'user'), 'XDG_CONFIG_DIRS': str(root / 'system')}):
                self.assertEqual(common.config('terminal')['size'], '90:30')

    def test_rate_units_switch_together(self):
        self.assertEqual(panel.format_rates(1024, 2048), '↓1/↑2 KiB/s')
        self.assertIn('MiB/s', panel.format_rates(11 * 1024**2, 1024))
        self.assertIn('KiB/s', panel.format_rates(10 * 1024**2, 0))

    def test_bridge_uses_physical_uplink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysroot, proc = root / 'sys', root / 'proc'
            for name in ['vmbr0', 'enp6s0', 'lo']:
                interface = sysroot / name
                (interface / 'statistics').mkdir(parents=True)
                (interface / 'statistics/rx_bytes').write_text('100')
                (interface / 'carrier').write_text('1')
            (sysroot / 'enp6s0/device').mkdir()
            (sysroot / 'vmbr0/brif/enp6s0').mkdir(parents=True)
            (proc / 'net').mkdir(parents=True)
            (proc / 'net/route').write_text('Iface Destination Gateway Flags RefCnt Use Metric Mask\nvmbr0 00000000 01010101 0003 0 0 0 00000000\n')
            with patch.dict(os.environ, {'VTM_NET_INTERFACE': ''}):
                self.assertEqual(panel.select_interface(sysroot, proc), 'enp6s0')

    def test_drm_device_from_connected_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'card1-HDMI-A-1').mkdir()
            (root / 'card1-HDMI-A-1/status').write_text('connected')
            self.assertEqual(capture.drm_device(sys_root=root), '/dev/dri/card1')

    def test_clipboard_sends_png_to_wayland(self):
        with tempfile.NamedTemporaryFile() as image:
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-test'}), patch.object(shutil, 'which', return_value='/usr/bin/wl-copy'), patch.object(subprocess, 'run') as run:
                clipboard.copy_file(image.name, 'image/png')
                self.assertEqual(run.call_args.args[0], ['wl-copy', '--type', 'image/png'])

    def test_clipboard_sends_png_to_x11(self):
        with tempfile.NamedTemporaryFile() as image:
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': '', 'DISPLAY': ':17'}), patch.object(shutil, 'which', return_value='/usr/bin/xclip'), patch.object(subprocess, 'run') as run:
                clipboard.copy_file(image.name, 'image/png')
                self.assertEqual(run.call_args.args[0], ['xclip', '-selection', 'clipboard', '-target', 'image/png'])

    def test_wallpaper_real_chafa_and_configuration_preservation(self):
        if not shutil.which('chafa') or not shutil.which('ffmpeg'):
            self.skipTest('chafa is unavailable')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            image = work / 'input.png'
            def chunk(tag, data):
                return struct.pack('!I', len(data)) + tag + data + struct.pack('!I', zlib.crc32(tag + data))
            image.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 2, 2, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff\x00\x00' * 2) * 2)) + chunk(b'IEND', b''))
            settings = work / 'config/vtm/settings.xml'
            settings.parent.mkdir(parents=True)
            settings.write_text('<config><desktop><taskbar><item id="keep"/></taskbar></desktop></config>')
            env = {**os.environ, 'XDG_CONFIG_HOME': str(work / 'config')}
            subprocess.run([root / 'bin/setwallpaper', '--mode', 'stretch', '--size', '8x4', '--no-reload', image], env=env, check=True, stdout=subprocess.DEVNULL)
            self.assertIn('id="keep"', settings.read_text())
            self.assertIn('<background><tile=', settings.read_text())
            self.assertEqual((settings.parent / 'wallpaper.source').read_text().strip(), str(image))
            subprocess.run([root / 'bin/setwallpaper', '--mode', 'cover', '--size', '8x4', '--no-reload', image], env=env, check=True, stdout=subprocess.DEVNULL)
            subprocess.run([root / 'bin/setwallpaper', '--restore', '--size', '12x6', '--no-reload'], env=env, check=True, stdout=subprocess.DEVNULL)
            self.assertEqual((settings.parent / 'wallpaper.size').read_text().strip(), '12x6')


if __name__ == '__main__':
    unittest.main()
