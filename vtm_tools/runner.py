"""Open an argument vector in a new VTM terminal; no shell interpolation."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import sys
from .common import config, lua_quote, monitor


def main():
    parser = argparse.ArgumentParser(description="Run a program in a new window of an existing VTM desktop")
    parser.add_argument("--pin")
    parser.add_argument("--title")
    parser.add_argument("--size", default=config("terminal").get("size", "110:40"))
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.command
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        argv = [os.getenv("SHELL", "/bin/sh"), "-l"]
    executable = shutil.which(argv[0])
    if not executable:
        parser.error("Executable not found: " + argv[0])
    if not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", args.size):
        parser.error("--size must be COLUMNS:ROWS")
    if not args.cwd.is_dir():
        parser.error("--cwd must be a directory")
    argv[0] = str(Path(executable).absolute())
    payload = base64.urlsafe_b64encode(json.dumps({"argv": argv, "cwd": str(args.cwd.resolve())}, ensure_ascii=False).encode()).decode()
    helper = Path(sys.argv[0]).resolve().parent / "vtm-tools-exec"
    # VTM has its own command-line parser: quote its helper path, and use only
    # URL-safe base64 for user arguments (including quotes, Unicode and newlines).
    quoted_helper = '"' + str(helper).replace('\\', '\\\\').replace('"', '\\"') + '"'
    attributes = {"id": "vtm-terminal", "type": "term", "title": args.title or Path(argv[0]).name,
        "cmd": quoted_helper + " " + payload, "cwd": str(args.cwd.resolve()),
        "winsize": args.size, "hidden": "true", "fit_viewport": "true",
        "wincoor_centered": "true", "wincoor_cascade": "true"}
    monitor("vtm.desktop.Run({" + ",".join(k + "=" + lua_quote(v) for k, v in attributes.items()) + "});", args.pin)


def execute():
    if len(sys.argv) != 2:
        raise ValueError("This helper expects one encoded argument vector")
    payload = json.loads(base64.urlsafe_b64decode(sys.argv[1]))
    argv = payload["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and "\0" not in a for a in argv):
        raise ValueError("Invalid argument vector")
    os.chdir(payload["cwd"])
    os.execv(argv[0], argv)
