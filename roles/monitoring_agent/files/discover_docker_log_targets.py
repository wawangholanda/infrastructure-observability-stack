#!/usr/bin/env python3
"""Discover active Docker JSON log files without reading log contents."""

import json
import os
import subprocess
import sys


def run_docker(*args):
    result = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def main():
    if len(sys.argv) != 1:
        raise SystemExit("This script does not accept arguments")

    containers = []

    for line in run_docker("ps", "--format", "json").splitlines():
        if not line.strip():
            continue
        containers.append(json.loads(line))

    targets = []
    for container in containers:
        container_id = container.get("ID")
        if not container_id:
            print(
                "Skipping container without an ID",
                file=sys.stderr,
            )
            continue

        inspected = json.loads(
            run_docker("inspect", container_id)
        )[0]

        log_config = inspected.get("HostConfig", {}).get("LogConfig", {})
        log_path = inspected.get("LogPath", "")
        name = inspected.get("Name", "").lstrip("/")
        image = inspected.get("Config", {}).get("Image", "")

        if log_config.get("Type") != "json-file":
            print(
                f"Skipping {name!r}: logging driver is not json-file",
                file=sys.stderr,
            )
            continue

        if not log_path.startswith("/") or not os.path.isfile(log_path):
            print(
                f"Skipping {name!r}: Docker log file is unavailable",
                file=sys.stderr,
            )
            continue

        targets.append(
            {
                "path": "/host/root" + log_path,
                "container": name,
                "image": image,
            }
        )

    print(json.dumps(sorted(targets, key=lambda item: item["container"])))


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"Docker log target discovery failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
