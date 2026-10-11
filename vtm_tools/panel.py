#!/usr/bin/env python3
"""Jednowierszowy panel VTM z transferem na aktywnej karcie sieciowej."""
import argparse
import datetime
import os
from pathlib import Path
import shutil
import time


def read_text(path, default=''):
    try:
        return path.read_text().strip()
    except OSError:
        return default


def default_routes(proc_root):
    routes = []
    for line in read_text(proc_root / 'net/route').splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 8:
            try:
                if fields[1] == '00000000' and fields[7] == '00000000' and int(fields[3], 16) & 1:
                    routes.append((int(fields[6]), fields[0]))
            except ValueError:
                continue
    if routes:
        return [name for _, name in sorted(routes)]
    for line in read_text(proc_root / 'net/ipv6_route').splitlines():
        fields = line.split()
        if len(fields) >= 10:
            try:
                if fields[0] == '0' * 32 and fields[1] == '00' and int(fields[8], 16) & 1:
                    routes.append((int(fields[5], 16), fields[9]))
            except ValueError:
                continue
    return [name for _, name in sorted(routes)]


def select_interface(sys_root=Path('/sys/class/net'), proc_root=Path('/proc')):
    names = sorted(p.name for p in sys_root.iterdir() if (p / 'statistics/rx_bytes').exists())
    override = os.getenv('VTM_NET_INTERFACE', '')
    if override in names:
        return override

    def online(name):
        return read_text(sys_root / name / 'carrier') == '1'

    def physical(name):
        return (sys_root / name / 'device').exists()

    def uplink(name, seen=None):
        seen = set() if seen is None else seen
        if name in seen:
            return name
        seen.add(name)
        directory = sys_root / name
        active_slave = read_text(directory / 'bonding/active_slave')
        if active_slave in names:
            return uplink(active_slave, seen)
        bridge = directory / 'brif'
        children = [p.name for p in bridge.iterdir()] if bridge.is_dir() else []
        children += [p.name[6:] for p in directory.glob('lower_*')]
        children += read_text(directory / 'bonding/slaves').split()
        children = sorted(set(n for n in children if n in names and n not in seen),
                          key=lambda n: (not online(n), not physical(n), n))
        for child in children:
            candidate = uplink(child, seen.copy())
            if physical(candidate) and online(candidate):
                return candidate
        return name

    for route in default_routes(proc_root):
        if route in names and route != 'lo':
            return uplink(route)
    for name in sorted(names, key=lambda n: (not physical(n), not online(n), n)):
        if name != 'lo' and online(name):
            return uplink(name)
    return next((name for name in names if name != 'lo' and physical(name)), None)


def sample():
    with open('/proc/stat') as f:
        values = [int(x) for x in f.readline().split()[1:9]]
    with open('/proc/meminfo') as f:
        mem = {line.split(':')[0]: int(line.split()[1]) for line in f}
    dev = select_interface()
    counters = Path('/sys/class/net') / (dev or '') / 'statistics'
    rx = int(read_text(counters / 'rx_bytes', '0'))
    tx = int(read_text(counters / 'tx_bytes', '0'))
    return sum(values), values[3] + values[4], mem, rx, tx, dev


def format_rates(rx, tx):
    if max(rx, tx) > 10 * 1024 * 1024:
        return f'↓{rx / 1024**2:.1f}/↑{tx / 1024**2:.1f} MiB/s'
    return f'↓{int(rx / 1024)}/↑{int(tx / 1024)} KiB/s'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', help='Force the network interface (default: route/uplink detection)')
    parser.add_argument('--once', action='store_true', help='Print one sample and exit')
    args = parser.parse_args()
    if args.interface:
        os.environ['VTM_NET_INTERFACE'] = args.interface
    days = ['poniedziałek', 'wtorek', 'środa', 'czwartek', 'piątek', 'sobota', 'niedziela']
    prev = sample()
    then = time.monotonic()
    print('\033[?1049h\033[?25l', end='', flush=True)
    try:
        while True:
            time.sleep(1)
            current = sample()
            now = time.monotonic()
            elapsed = now - then
            total = current[0] - prev[0]
            cpu = round(100 * (total - current[1] + prev[1]) / total) if total else 0
            ram = round(100 * (1 - current[2]['MemAvailable'] / current[2]['MemTotal']))
            same_interface = current[5] == prev[5]
            rx = max(0, (current[3] - prev[3]) / elapsed) if same_interface else 0
            tx = max(0, (current[4] - prev[4]) / elapsed) if same_interface else 0
            dt = datetime.datetime.now()
            right = f'{days[dt.weekday()]} {dt.day} {dt:%H:%M}'
            network = f'{current[5]} {format_rates(rx, tx)}' if current[5] else 'brak połączenia'
            left = f'CPU {cpu}% | RAM {ram}% | NET {network}'
            cols = shutil.get_terminal_size((120, 1)).columns
            width = max(0, cols - 3)
            right = right[-width:] if width else ''
            left = left[:max(0, width - len(right) - 1)]
            line = ' ' + left + ' ' * max(0, width - len(left) - len(right)) + right + ' '
            print('\r\033[?7l\033[38;2;205;214;244m\033[48;2;24;24;37m\033[2K' + line[:max(0, cols - 1)] + '\r\033[0m', end='', flush=True)
            prev, then = current, now
            if args.once:
                break
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        print('\033[?25h\033[?7h\033[0m\033[?1049l', end='', flush=True)


if __name__ == '__main__':
    main()
