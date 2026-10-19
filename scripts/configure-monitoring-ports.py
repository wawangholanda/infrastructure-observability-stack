#!/usr/bin/env python3
"""Prompt for monitoring host ports and persist them consistently."""

import argparse
import os
import sys
import tempfile
from pathlib import Path

import yaml


PORTS = {
    "grafana_host_port": (("lab", "grafana", "host_port"), 3000),
    "prometheus_host_port": (
        ("lab", "monitoring", "prometheus_remote_write_port"),
        9090,
    ),
    "loki_host_port": (("lab", "monitoring", "loki_push_port"), 3100),
}


def require_mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a YAML mapping.")
    return value


def set_nested(data, keys, value):
    current = data
    for key in keys[:-1]:
        current = require_mapping(current.get(key), ".".join(keys[:-1]))
    current[keys[-1]] = value


def read_yaml(path):
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read valid YAML from {path}: {exc}") from exc
    return require_mapping(data, str(path))


def prompt_port(label, default):
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            port = int(raw)
        except ValueError:
            port = 0
        if 1 <= port <= 65535:
            return port
        print("Enter a port number between 1 and 65535.", file=sys.stderr)


def atomic_write_bytes(path, content, mode):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(config_dir):
    paths = {
        "profile": config_dir / ".secrets/assets/lab.yml",
        "inventory": config_dir / "inventory/lab/hosts.yml",
        "bind": config_dir / "inventory/local/group_vars/monitoring.yml",
    }

    profile = read_yaml(paths["profile"])
    inventory = read_yaml(paths["inventory"])
    bind_vars = read_yaml(paths["bind"])

    lab = require_mapping(profile.get("lab"), "lab")
    grafana = require_mapping(lab.get("grafana"), "lab.grafana")
    monitoring = require_mapping(lab.get("monitoring"), "lab.monitoring")
    all_vars = require_mapping(inventory.get("all"), "all")
    all_vars = require_mapping(all_vars.get("vars"), "all.vars")

    current_values = {
        "grafana_host_port": grafana.get("host_port", 3000),
        "prometheus_host_port": monitoring.get("prometheus_remote_write_port", 9090),
        "loki_host_port": monitoring.get("loki_push_port", 3100),
    }

    selected = {}
    for name, (_, first_run_default) in PORTS.items():
        saved = current_values[name]
        if (
            isinstance(saved, bool)
            or not isinstance(saved, int)
            or not 1 <= saved <= 65535
        ):
            saved = first_run_default
        selected[name] = prompt_port(name.replace("_", " ").title(), saved)

    for name, (keys, _) in PORTS.items():
        set_nested(profile, keys, selected[name])

    all_vars["prometheus_remote_write_port"] = selected["prometheus_host_port"]
    all_vars["loki_push_port"] = selected["loki_host_port"]
    bind_vars.update(selected)

    documents = {
        paths["profile"]: yaml.safe_dump(profile, sort_keys=False).encode("utf-8"),
        paths["inventory"]: yaml.safe_dump(inventory, sort_keys=False).encode("utf-8"),
        paths["bind"]: yaml.safe_dump(bind_vars, sort_keys=False).encode("utf-8"),
    }
    originals = {
        path: (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in documents
    }

    try:
        for path, content in documents.items():
            atomic_write_bytes(path, content, 0o600)
    except OSError as write_error:
        rollback_errors = []
        for path, (content, mode) in originals.items():
            try:
                atomic_write_bytes(path, content, mode)
            except OSError as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise OSError(
                f"Configuration write failed ({write_error}); "
                f"rollback also failed ({details})"
            ) from write_error
        raise

    print("Monitoring host ports saved:")
    for name, port in selected.items():
        print(f"  {name}: {port}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        required=True,
        help="Repository/configuration root directory",
    )
    args = parser.parse_args()
    try:
        configure(args.config_dir.resolve())
    except (EOFError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
