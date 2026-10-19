import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap-lab.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_lab", SCRIPT)
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)


class BootstrapLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bootstrap-lab-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_bootstrap(self, answers=None, passwords=None):
        answers = iter(answers or [])
        passwords = iter(passwords or [])
        with (
            patch(
                "sys.argv",
                ["bootstrap-lab.py", "--config-dir", str(self.root)],
            ),
            patch("builtins.input", side_effect=lambda _: next(answers)),
            patch(
                "getpass.getpass",
                side_effect=lambda _: next(passwords),
            ),
        ):
            return BOOTSTRAP.main()

    def test_creates_server_client_inventory_and_secure_files(self):
        password = "LabBootstrapTest123!"
        answers = [
            "192.168.50.10",  # monitoring server
            "",               # client-01 name
            "192.168.50.11",  # client-01 IP
            "y",              # add another client
            "",               # client-02 name
            "192.168.50.12",  # client-02 IP
            "n",              # done adding clients
            "",               # endpoint defaults to server IP
            "",               # SSH user defaults to debian
            "2",              # use SSH password
            "",               # sudo/become password required
            "", "",           # Grafana bind addresses
            "",               # Grafana host port defaults to 3000
            "",               # Prometheus host port defaults to 9090
            "",               # Loki host port defaults to 3100
            "",               # confirm writing config
            "",               # Grafana username
        ]

        result = self.run_bootstrap(
            answers=answers,
            passwords=[password, password],
        )

        self.assertEqual(result, 0)

        profile_path = self.root / ".secrets/assets/lab.yml"
        inventory_path = self.root / "inventory/lab/hosts.yml"
        bind_path = self.root / "inventory/local/group_vars/monitoring.yml"
        env_path = self.root / ".secrets/.env"

        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))

        groups = inventory["all"]["children"]
        self.assertEqual(
            groups["monitoring"]["hosts"]["monitoring-server"]["ansible_host"],
            "192.168.50.10",
        )
        client_hosts = groups["monitoring_clients"]["hosts"]
        self.assertEqual(
            client_hosts,
            {
                "server1": {"ansible_host": "192.168.50.11"},
                "server2": {"ansible_host": "192.168.50.12"},
            },
        )
        self.assertEqual(
            [client["name"] for client in profile["lab"]["servers"]["clients"]],
            ["server1", "server2"],
        )
        self.assertEqual(profile["lab"]["ssh"]["auth_method"], "password")
        self.assertIsNone(profile["lab"]["ssh"]["private_key"])
        self.assertEqual(profile["lab"]["grafana"]["host_port"], 3000)
        self.assertEqual(
            profile["lab"]["monitoring"]["prometheus_remote_write_port"], 9090
        )
        self.assertEqual(profile["lab"]["monitoring"]["loki_push_port"], 3100)
        self.assertEqual(
            inventory["all"]["vars"]["prometheus_remote_write_port"], 9090
        )
        self.assertEqual(inventory["all"]["vars"]["loki_push_port"], 3100)
        self.assertNotIn(password, profile_path.read_text(encoding="utf-8"))
        self.assertNotIn(password, inventory_path.read_text(encoding="utf-8"))

        for path in (profile_path, inventory_path, bind_path, env_path):
            with self.subTest(path=path):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        self.assertEqual(
            yaml.safe_load(bind_path.read_text(encoding="utf-8")),
            {
                "grafana_wireguard_bind_ip": "127.0.0.1",
                "grafana_lan_bind_ip": "192.168.50.10",
                "grafana_host_port": 3000,
                "prometheus_host_port": 9090,
                "loki_host_port": 3100,
                "monitoring_listen_ip": "192.168.50.10",
            },
        )

    def test_prompt_port_rejects_invalid_values_then_accepts_valid_port(self):
        with patch("builtins.input", side_effect=["abc", "0", "65536", "3002"]):
            self.assertEqual(BOOTSTRAP.prompt_port("Grafana host port"), 3002)

    def test_prompt_port_accepts_boundary_values(self):
        with patch("builtins.input", side_effect=["1"]):
            self.assertEqual(BOOTSTRAP.prompt_port("Grafana host port"), 1)
        with patch("builtins.input", side_effect=["65535"]):
            self.assertEqual(BOOTSTRAP.prompt_port("Grafana host port"), 65535)

    def test_monitoring_compose_exposes_remote_ingest_ports_on_configured_ip(self):
        compose = (
            Path(__file__).resolve().parents[1]
            / "files"
            / "monitoring"
            / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("--web.enable-remote-write-receiver", compose)
        self.assertIn('{{ monitoring_listen_ip }}:{{ prometheus_host_port }}:9090', compose)
        self.assertIn('{{ monitoring_listen_ip }}:{{ loki_host_port }}:3100', compose)

    def test_existing_configuration_is_preserved(self):
        profile_path = self.root / ".secrets/assets/lab.yml"
        inventory_path = self.root / "inventory/lab/hosts.yml"
        bind_path = self.root / "inventory/local/group_vars/monitoring.yml"
        env_path = self.root / ".secrets/.env"

        for path, content in (
            (profile_path, "existing profile\n"),
            (inventory_path, "existing inventory\n"),
            (bind_path, "existing bind vars\n"),
            (env_path, "existing secrets\n"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        snapshots = {
            path: path.read_bytes()
            for path in (profile_path, inventory_path, bind_path, env_path)
        }

        result = self.run_bootstrap()

        self.assertEqual(result, 0)
        for path, content in snapshots.items():
            with self.subTest(path=path):
                self.assertEqual(path.read_bytes(), content)

    def test_partial_configuration_is_rejected_without_overwrite(self):
        profile_path = self.root / ".secrets/assets/lab.yml"
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text("keep this\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Partial lab configuration"):
            self.run_bootstrap()

        self.assertEqual(profile_path.read_text(encoding="utf-8"), "keep this\n")
        self.assertFalse((self.root / "inventory/lab/hosts.yml").exists())

    def test_secure_file_refuses_to_overwrite_existing_file(self):
        path = self.root / ".secrets/test.yml"
        path.parent.mkdir(parents=True)
        path.write_text("original\n", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            BOOTSTRAP.write_secure_file(path, "replacement\n")

        self.assertEqual(path.read_text(encoding="utf-8"), "original\n")


if __name__ == "__main__":
    unittest.main()
