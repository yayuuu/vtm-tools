import argparse
import sys
from .common import monitor


def main():
    parser = argparse.ArgumentParser(description="Execute Lua in an existing VTM desktop monitor")
    parser.add_argument("--pin")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("script", help="Lua script, or - to read stdin")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    script = sys.stdin.read() if args.script == "-" else args.script
    print(monitor(script, args.pin, args.timeout), end="")
