#!/usr/bin/env python3
"""Interactively create a secure local lab profile and Ansible inventory."""

import argparse
import getpass
import ipaddress
import os
import re
import secrets
import sys
from pathlib import Path

import yaml


def prompt_required(label, default=None):
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix}: ").strip()
        if not value and default is not None:
            value = default
        if value:
            if "\n" not in value and "\r" not in value:
                return value
        print("A non-empty, single-line value is required.")


def prompt_yes_no(label, default=True):
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def prompt_ipv4(label, default):
    while True:
        value = prompt_required(label, default)
        try:
            address = ipaddress.ip_address(value)
            if address.version == 4:
                return str(address)
        except ValueError:
            pass
        print("Enter a valid IPv4 address.")


def prompt_port(label, default=3002):
    while True:
        value = prompt_required(label, str(default))
        try:
            port = int(value)
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
        print("Enter a port number between 1 and 65535.")


def prompt_password(label, minimum_length=1):
    while True:
        value = getpass.getpass(f"{label}: ")
        if len(value) < minimum_length:
            print(f"Password must contain at least {minimum_length} characters.")
            continue
        if "\n" in value or "\r" in value:
            print("Password must be a single-line value.")
            continue
        return value


def write_secure_file(path, content, secure_parent=False):
    """Create a file with mode 0600 without restricting ordinary config directories."""
    if secure_parent:
        # Apply 0700 when creating a new directory; preserve existing permissions.
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o600)
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def quote_env_value(value):
    """Quote a Docker Compose .env value as a literal single-quoted string."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def create_env_file(path):
    print("\n=== Grafana credentials ===")
    username = prompt_required("Grafana admin username", "admin")
    password = prompt_password("Grafana admin password (minimum 12 characters)", 12)
    confirmation = getpass.getpass("Confirm Grafana admin password: ")
    if password != confirmation:
        raise ValueError("Grafana passwords do not match.")

    content = (
        f"GRAFANA_ADMIN_USER={quote_env_value(username)}\n"
        f"GRAFANA_ADMIN_PASSWORD={quote_env_value(password)}\n"
        f"GRAFANA_SECRET_KEY={quote_env_value(secrets.token_urlsafe(32))}\n"
    )
    write_secure_file(path, content, secure_parent=True)
    print("Created Grafana environment file with permission 600.")


def build_profile():
    print("\n=== Monitoring server ===")
    server_ip = prompt_ipv4("Monitoring server IPv4", None)

    print("\n=== Monitoring clients ===")
    print("Add one or more clients. Each client needs a unique name and IPv4 address.")

    clients = []
    client_names = set()
    client_addresses = {server_ip}

    while True:
        index = len(clients) + 1

        while True:
            name = prompt_required(f"Client {index} inventory name", f"server{index}")
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", name):
                print("Use only letters, digits, underscore, or hyphen; do not start with a hyphen.")
                continue
            if name in client_names or name == "monitoring-server":
                print("That client name is already in use.")
                continue
            break

        while True:
            address = prompt_ipv4(f"Client {index} IPv4", None)
            if address in client_addresses:
                print("This address is already assigned to the monitoring server or another client.")
                continue
            break

        clients.append({
            "name": name,
            "ansible_host": address,
            "groups": ["monitoring_clients"],
        })
        client_names.add(name)
        client_addresses.add(address)

        if not prompt_yes_no("Add another monitoring client?", False):
            break

    print("\n=== Monitoring endpoint ===")
    endpoint = prompt_required(
        "Monitoring endpoint as seen from the clients", server_ip
    )

    print("\n=== SSH authentication ===")
    ssh_user = prompt_required("SSH username", "debian")
    auth_method = ""
    while auth_method not in {"1", "2"}:
        print("1) Private key (recommended)")
        print("2) SSH password (asked securely when quick-start runs)")
        auth_method = input("Select authentication method [1]: ").strip() or "1"
        if auth_method not in {"1", "2"}:
            print("Choose 1 or 2.")

    private_key = None
    if auth_method == "1":
        default_key = str(Path.home() / ".ssh" / "id_rsa")
        while True:
            key_input = prompt_required("SSH private key path", default_key)
            key_path = Path(key_input).expanduser().resolve()
            if key_path.is_file() and os.access(key_path, os.R_OK):
                private_key = str(key_path)
                break
            print(f"Cannot read private key file: {key_path}")
            if not prompt_yes_no("Enter a different key path?", True):
                raise ValueError(
                    "A readable private key is required for key authentication."
                )

    become_password_required = prompt_yes_no(
        "Does sudo/become require a password on these VMs?", True
    )

    print("\n=== Grafana bind addresses ===")
    print("For a VM without WireGuard, keep the first address at 127.0.0.1.")
    wireguard_ip = prompt_ipv4(
        "Grafana WireGuard bind IP (loopback is safe for this lab)",
        "127.0.0.1",
    )
    lan_ip = prompt_ipv4("Grafana LAN bind IP", server_ip)
    if wireguard_ip == lan_ip:
        raise ValueError(
            "Grafana WireGuard and LAN bind addresses must differ; "
            "binding the same IP and port twice can prevent startup."
        )
    grafana_host_port = prompt_port("Grafana host port", 3000)
    prometheus_host_port = prompt_port("Prometheus host port", 9090)
    loki_host_port = prompt_port("Loki host port", 3100)

    profile = {
        "lab": {
            "ssh": {
                "user": ssh_user,
                "auth_method": "key" if auth_method == "1" else "password",
                "private_key": private_key,
                "become": True,
                "become_password_required": become_password_required,
            },
            "servers": {
                "monitoring": {
                    "name": "monitoring-server",
                    "ansible_host": server_ip,
                    "groups": ["monitoring"],
                },
                "clients": clients,
            },
            "monitoring": {
                "endpoint_host": endpoint,
                "prometheus_remote_write_port": prometheus_host_port,
                "loki_push_port": loki_host_port,
            },
            "grafana": {
                "wireguard_bind_ip": wireguard_ip,
                "lan_bind_ip": lan_ip,
                "host_port": grafana_host_port,
            },
        }
    }

    inventory_vars = {
        "ansible_user": ssh_user,
        "ansible_become": True,
        "monitoring_endpoint_host": endpoint,
        "prometheus_remote_write_port": prometheus_host_port,
        "loki_push_port": loki_host_port,
    }
    if private_key:
        inventory_vars["ansible_ssh_private_key_file"] = private_key

    client_hosts = {
        client["name"]: {"ansible_host": client["ansible_host"]}
        for client in clients
    }
    inventory = {
        "all": {
            "vars": inventory_vars,
            "children": {
                "monitoring": {
                    "hosts": {
                        "monitoring-server": {"ansible_host": server_ip}
                    }
                },
                "monitoring_clients": {"hosts": client_hosts},
            },
        }
    }

    bind_vars = {
        "grafana_wireguard_bind_ip": wireguard_ip,
        "grafana_lan_bind_ip": lan_ip,
        "grafana_host_port": grafana_host_port,
        "prometheus_host_port": prometheus_host_port,
        "loki_host_port": loki_host_port,
        "monitoring_listen_ip": server_ip,
    }

    return profile, inventory, bind_vars


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap the local lab profile, inventory, and Grafana settings."
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("."),
        help="Repository root (defaults to the current directory).",
    )
    args = parser.parse_args()
    root = args.config_dir.expanduser().resolve()

    profile_path = root / ".secrets" / "assets" / "lab.yml"
    inventory_path = root / "inventory" / "lab" / "hosts.yml"
    bind_vars_path = root / "inventory" / "local" / "group_vars" / "monitoring.yml"
    env_path = root / ".secrets" / ".env"

    core_paths = (profile_path, inventory_path, bind_vars_path)
    existing_core = [path for path in core_paths if path.exists()]
    if existing_core and len(existing_core) != len(core_paths):
        missing = [str(path.relative_to(root)) for path in core_paths if not path.exists()]
        raise ValueError(
            "Partial lab configuration detected; refusing to overwrite or mix files. "
            "Missing: " + ", ".join(missing)
        )

    created = []
    try:
        if not existing_core:
            profile, inventory, bind_vars = build_profile()
            lab = profile["lab"]
            print("\n=== Configuration summary ===")
            print(f"Monitoring server: {lab['servers']['monitoring']['ansible_host']}")
            print(f"Monitoring clients: {len(lab['servers']['clients'])}")
            for client in lab["servers"]["clients"]:
                print(f"  - {client['name']}: {client['ansible_host']}")
            print(f"SSH username:      {lab['ssh']['user']}")
            print(f"SSH authentication: {lab['ssh']['auth_method']}")
            print("SSH/sudo passwords will not be stored in the profile or inventory.")
            if not prompt_yes_no("Write lab profile and inventory", True):
                print("Cancelled; no lab files were created.")
                return 0

            files = (
                (profile_path, yaml.safe_dump(profile, sort_keys=False)),
                (inventory_path, yaml.safe_dump(inventory, sort_keys=False)),
                (bind_vars_path, yaml.safe_dump(bind_vars, sort_keys=False)),
            )
            for path, content in files:
                write_secure_file(path, content, secure_parent=path == profile_path)
                created.append(path)
            print("Created lab profile, inventory, and Grafana bind settings.")

        else:
            print("Lab profile, inventory, and bind settings already exist; preserving them.")

        if not env_path.exists():
            create_env_file(env_path)
            created.append(env_path)
        else:
            print("Grafana environment file already exists; preserving it.")

        print("\nLab bootstrap completed.")
        print(f"Profile:   {profile_path.relative_to(root)}")
        print(f"Inventory: {inventory_path.relative_to(root)}")
        print(f"Bind vars: {bind_vars_path.relative_to(root)}")
        print(f"Secrets:   {env_path.relative_to(root)}")
        return 0

    except Exception:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
