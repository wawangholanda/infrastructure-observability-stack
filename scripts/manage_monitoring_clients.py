#!/usr/bin/env python3
"""Safely add a monitoring client to an existing Ansible inventory."""

import argparse
import ipaddress
import os
import re
import stat
import tempfile
from pathlib import Path

import yaml


NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def validate_name(name):
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Invalid hostname: use letters, digits, underscore, or hyphen; "
            "the first character must not be a hyphen."
        )
    return name


def validate_ipv4(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Invalid IPv4 address.") from exc

    if address.version != 4:
        raise ValueError("Only IPv4 addresses are supported.")

    return str(address)


def collect_hosts(node, path="all"):
    """Collect inventory hostnames and addresses recursively."""
    names = {}
    addresses = {}

    if not isinstance(node, dict):
        return names, addresses

    hosts = node.get("hosts", {})
    if isinstance(hosts, dict):
        for name, details in hosts.items():
            address = details.get("ansible_host") if isinstance(details, dict) else None
            names.setdefault(name, []).append(path)
            if isinstance(address, str):
                addresses.setdefault(address, []).append(name)

    children = node.get("children", {})
    if isinstance(children, dict):
        for child_name, child in children.items():
            child_names, child_addresses = collect_hosts(
                child, f"{path}/{child_name}"
            )
            for name, locations in child_names.items():
                names.setdefault(name, []).extend(locations)
            for address, hostnames in child_addresses.items():
                addresses.setdefault(address, []).extend(hostnames)

    return names, addresses


def atomic_write(path, content, mode):
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)

        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def add_client(inventory_path, name, address):
    path = Path(inventory_path).expanduser()

    if path.is_symlink():
        raise ValueError("Refusing to modify an inventory symlink.")
    if not path.is_file():
        raise ValueError(f"Inventory file does not exist: {path}")

    name = validate_name(name)
    address = validate_ipv4(address)

    original = path.read_text(encoding="utf-8")
    inventory = yaml.safe_load(original)

    if not isinstance(inventory, dict) or not isinstance(inventory.get("all"), dict):
        raise ValueError("Inventory must contain an 'all' mapping.")

    children = inventory["all"].get("children")
    if not isinstance(children, dict):
        raise ValueError("Inventory must define all.children.")

    clients_group = children.get("monitoring_clients")
    if not isinstance(clients_group, dict):
        raise ValueError(
            "Inventory has no monitoring_clients group. "
            "Migrate the inventory structure before onboarding clients."
        )

    client_hosts = clients_group.setdefault("hosts", {})
    if not isinstance(client_hosts, dict):
        raise ValueError("monitoring_clients.hosts must be a mapping.")

    existing_names, existing_addresses = collect_hosts(inventory["all"])

    if name in existing_names:
        locations = ", ".join(existing_names[name])
        raise ValueError(f"Hostname '{name}' already exists in inventory: {locations}")

    if address in existing_addresses:
        owners = ", ".join(existing_addresses[address])
        raise ValueError(f"IP address '{address}' is already assigned to: {owners}")

    client_hosts[name] = {"ansible_host": address}
    content = yaml.safe_dump(inventory, sort_keys=False, allow_unicode=True)
    mode = stat.S_IMODE(path.stat().st_mode)
    atomic_write(path, content, mode)


def main():
    parser = argparse.ArgumentParser(
        description="Add a monitoring client to the generic Ansible inventory."
    )
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--name", required=True, help="Ansible inventory hostname")
    parser.add_argument("--ip", required=True, help="Client IPv4 address")
    args = parser.parse_args()

    add_client(args.inventory, args.name, args.ip)
    print(f"Added monitoring client '{args.name}' ({args.ip}).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(f"ERROR: {exc}")
