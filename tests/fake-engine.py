#!/usr/bin/env python3
"""Record engine calls and execute bind-mount probes without a daemon."""

import json
import os
from pathlib import Path
import subprocess
import sys


args = sys.argv[1:]
with open(os.environ["ENGINE_LOG"], "a") as log:
    log.write(json.dumps([Path(sys.argv[0]).name, *args]) + "\n")

if args[0] == "run" and "-it" in args and os.environ.get("ENGINE_ENV_LOG"):
    environment = {}
    for index, arg in enumerate(args):
        if arg == "-e":
            name, separator, value = args[index + 1].partition("=")
            environment[name] = value if separator else os.environ.get(name)
    Path(os.environ["ENGINE_ENV_LOG"]).write_text(
        json.dumps(environment)
    )

if args[0] == "info":
    if os.environ.get("FAIL_INFO"):
        print("permission denied connecting to Docker socket", file=sys.stderr)
        sys.exit(1)
    print(os.environ.get("SECURITY_OPTIONS", '["name=seccomp,profile=builtin"]'))
elif args[:2] == ["image", "inspect"]:
    sys.exit(int(os.environ.get("IMAGE_MISSING", "0")))
elif args[0] == "run" and "-it" not in args:
    mounts = []
    for index, arg in enumerate(args):
        if arg == "-v":
            source, target = args[index + 1].rsplit(":", 1)
            mounts.append((source, target))
    if os.environ.get("FAIL_MOUNT") in [target for _, target in mounts]:
        print("mock engine: bind mount permission denied", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("SKIP_PROBE"):
        sys.exit(0)
    image_index = args.index(os.environ.get("IMAGE_NAME", "codex-container"))
    command = args[image_index + 1:]
    for index, arg in enumerate(command):
        for source, target in mounts:
            if arg == target or arg.startswith(target + "/"):
                command[index] = source + arg[len(target):]
                break
    if os.environ.get("PROBE_USERNS"):
        if args[args.index("--user") + 1] != "0:0":
            sys.exit("rootless probe must run as namespace root")
        command = ["unshare", "--user", "--map-root-user", *command]
    sys.exit(subprocess.run(command).returncode)
elif args[0] not in ("build", "tag", "run"):
    sys.exit("unexpected engine command")
