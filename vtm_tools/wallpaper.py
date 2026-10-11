#!/usr/bin/env python3
"""Trwała tapeta ANSI dla vtm, z dopasowaniem do pulpitu i zmianą na żywo."""
import argparse
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from .common import atomic_write, config, lua_quote, monitor, session_pin, terminal_geometry


def xml_quote(text):
    return text.replace('\\', '\\\\').replace('"', '\\"').replace('\x1b', '\\e').replace('\n', '\\n').replace('\r', '\\r')


def reload_wallpaper(art, pin):
    # Keep rows reserved for a desktop panel out of the live background overlay.
    reserved = int(config('wallpaper').get('reserved_rows', 1))
    live_art = '\x1b[12:2p' + ''.join(f'\x1b[20:0:{row}p' + line
        for row, line in enumerate(art.splitlines()) if row >= reserved)
    try:
        monitor('vtm.desktop.SetOverlay(-100, ' + lua_quote(live_art) +
                '); vtm.desktop.Deface();', pin)
        return True
    except (OSError, RuntimeError):
        return False


def desktop_size(pin):
    rows, cols, _, _ = terminal_geometry(pin)
    return f'{cols}x{rows}'


def positioned_art(art):
    return '\x1b[12:2p' + ''.join(
        f'\x1b[20:0:{row}p' + line for row, line in enumerate(art.splitlines()))


def replace_background(content, replacement):
    # VTM XML accepts quoted attributes as elements, so ElementTree cannot parse it.
    pattern = r'<tile="(?:\\.|[^"\\])*"\s*/>'
    background = re.search(r'<background(?:\s[^>]*)?>(.*?)</background>', content, re.S)
    if background:
        body = background.group(1)
        body = re.sub(pattern, lambda _: replacement, body, count=1) if re.search(pattern, body) else body + replacement
        return content[:background.start(1)] + body + content[background.end(1):]
    block = '<background>' + replacement + '</background>'
    if '</desktop>' in content:
        return content.replace('</desktop>', block + '</desktop>', 1)
    if '</config>' in content:
        return content.replace('</config>', '<desktop>' + block + '</desktop></config>', 1)
    if not content.strip():
        return '<config><desktop>' + block + '</desktop></config>\n'
    raise ValueError('Settings must contain <config> or <desktop>')


def repeat_art(art, size):
    cols, rows = map(int, size.split('x'))
    tile_rows = art.splitlines()
    if not tile_rows or any(not re.sub(r'\x1b\[[0-9;:]*m', '', line) for line in tile_rows):
        raise ValueError('The wallpaper tile contains an empty row')
    expanded = []
    for index in range(rows):
        tokens = re.findall(r'\x1b\[[0-9;:]*m|[^\x1b]', tile_rows[index % len(tile_rows)])
        line, width = [], 0
        while width < cols:
            for token in tokens:
                if token.startswith('\x1b'):
                    line.append(token)
                elif width < cols:
                    line.append(token)
                    width += 1
                else:
                    break
        expanded.append(''.join(line) + '\x1b[0m')
    return '\n'.join(expanded)


def main():
    parser = argparse.ArgumentParser(description='Ustaw tapetę vtm z pliku PNG/JPG.')
    confdir = Path(os.getenv('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'vtm'
    defaults = config('wallpaper')
    parser.add_argument('image', type=Path, nargs='?')
    parser.add_argument('--restore', action='store_true', help='Re-render the saved image for this terminal size')
    parser.add_argument('--cell-size', default=defaults.get('cell_size'), help='Cell size in pixels, e.g. 8x16')
    parser.add_argument('--mode', choices=['cover', 'contain', 'stretch', 'tile'], default=defaults.get('mode', 'cover'),
                        help='cover: wypełnij i przytnij; contain: cały obraz; stretch: rozciągnij; tile: powtarzaj.')
    parser.add_argument('--size', help='Rozmiar w komórkach; domyślnie rozmiar pulpitu, dla tile 80x25.')
    parser.add_argument('--symbols', default=defaults.get('symbols', 'sextant+hhalf+vhalf+quad'),
                        help='Dozwolone glify chafa; domyślnie sextant+hhalf+vhalf+quad.')
    parser.add_argument('--pin', default=session_pin())
    parser.add_argument('--no-reload', action='store_true')
    args = parser.parse_args()
    if args.restore:
        saved = confdir / 'wallpaper.source'
        if not saved.exists():
            return
        if args.image is not None:
            parser.error('--restore cannot be combined with an image')
        args.image = Path(saved.read_text().strip())
        for name in ('mode', 'symbols'):
            saved = confdir / ('wallpaper.' + name)
            if saved.exists():
                setattr(args, name, saved.read_text().strip())
        if args.mode == 'tile' and not args.size and (confdir / 'wallpaper.size').exists():
            args.size = (confdir / 'wallpaper.size').read_text().strip()
    elif args.image is None:
        parser.error('Provide an image or --restore')
    args.size = args.size or ('80x25' if args.mode == 'tile' else desktop_size(args.pin))
    if args.mode not in ('cover', 'contain', 'stretch', 'tile'):
        parser.error('Invalid wallpaper mode')
    rows, cols, pixels_x, pixels_y = terminal_geometry(args.pin)
    cell_x, cell_y = (max(1, pixels_x // cols), max(1, pixels_y // rows)) if pixels_x and pixels_y else (8, 16)
    if args.cell_size:
        if not re.fullmatch(r'[1-9][0-9]{0,3}x[1-9][0-9]{0,3}', args.cell_size):
            parser.error('--cell-size must be WIDTHxHEIGHT')
        cell_x, cell_y = map(int, args.cell_size.split('x'))
    image = args.image.expanduser().resolve()
    if not image.is_file():
        parser.error(f'Nie ma pliku: {image}')
    if not re.fullmatch(r'[1-9][0-9]{0,3}x[1-9][0-9]{0,3}', args.size):
        parser.error('--size musi mieć postać 210x65')
    for program in ['chafa', 'ffmpeg']:
        if shutil.which(program) is None:
            parser.error(f'Brakuje programu: {program}')
    cols, rows = map(int, args.size.split('x'))
    with tempfile.TemporaryDirectory(prefix='vtm-wallpaper-') as workdir:
        prepared = image
        if args.mode in ('cover', 'contain'):
            width, height = cols * cell_x, rows * cell_y
            if args.mode == 'cover':
                vf = f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}'
            else:
                vf = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101018'
            prepared = Path(workdir) / 'prepared.png'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-i', str(image), '-vf', vf, '-frames:v', '1', str(prepared)], check=True)
        command = ['chafa', '--format=symbols', '--colors=full', '--symbols=' + args.symbols,
                   '--animate=off', '--polite=on', '--optimize=0',
                   '--size=' + args.size, '--view-size=' + args.size]
        if args.mode != 'tile':
            command += ['--stretch']
        art = subprocess.check_output(command + ['--', str(prepared)]).decode().rstrip('\r\n')

    confdir = Path(os.getenv('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'vtm'
    confdir.mkdir(parents=True, exist_ok=True)
    settings = confdir / 'settings.xml'
    replacement = '<tile="' + xml_quote(positioned_art(art)) + '"/>'
    with (confdir / 'wallpaper.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        content = settings.read_text() if settings.exists() else '<config></config>\n'
        atomic_write(settings, replace_background(content, replacement))
    (confdir / 'wallpaper.ans').write_text(art)
    (confdir / 'wallpaper.source').write_text(str(image) + '\n')
    (confdir / 'wallpaper.mode').write_text(args.mode + '\n')
    (confdir / 'wallpaper.size').write_text(args.size + '\n')
    (confdir / 'wallpaper.symbols').write_text(args.symbols + '\n')
    print(f'Zapisano tapetę: {image} ({args.mode}, {args.size}).', flush=True)
    live_art = repeat_art(art, desktop_size(args.pin)) if args.mode == 'tile' else art
    if not args.no_reload and reload_wallpaper(live_art, args.pin):
        print('Przeładowano tapetę w działającym vtm.')
    else:
        print('Tapeta będzie widoczna po ponownym połączeniu z vtm.')


if __name__ == '__main__':
    main()
