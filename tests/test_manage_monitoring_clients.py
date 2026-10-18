import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

import yaml


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "manage_monitoring_clients.py"
)
SPEC = importlib.util.spec_from_file_location("manage_monitoring_clients", SCRIPT)
CLIENTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENTS)


class ManageMonitoringClientsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="monitoring-clients-test-")
        self.inventory_path = Path(self.temp.name) / "hosts.yml"
        self.inventory = {
            "all": {
                "vars": {
                    "ansible_user": "debian",
                    "monitoring_endpoint_host": "192.168.50.10",
                },
                "children": {
                    "monitoring": {
                        "hosts": {
                            "monitoring-server": {
                                "ansible_host": "192.168.50.10"
                            }
                        }
                    },
                    "monitoring_clients": {
                        "hosts": {
                            "server1": {"ansible_host": "192.168.50.11"},
                            "server2": {"ansible_host": "192.168.50.12"},
                        }
                    },
                },
            }
        }
        self.inventory_path.write_text(
            yaml.safe_dump(self.inventory, sort_keys=False),
            encoding="utf-8",
        )
        self.inventory_path.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def read_inventory(self):
        return yaml.safe_load(self.inventory_path.read_text(encoding="utf-8"))

    def test_adds_client_and_preserves_existing_inventory(self):
        CLIENTS.add_client(self.inventory_path, "app-01", "192.168.50.13")

        result = self.read_inventory()
        groups = result["all"]["children"]
        self.assertEqual(
            groups["monitoring_clients"]["hosts"]["app-01"],
            {"ansible_host": "192.168.50.13"},
        )
        self.assertEqual(
            groups["monitoring"]["hosts"]["monitoring-server"]["ansible_host"],
            "192.168.50.10",
        )
        self.assertEqual(
            groups["monitoring_clients"]["hosts"]["server1"]["ansible_host"],
            "192.168.50.11",
        )
        self.assertEqual(stat.S_IMODE(self.inventory_path.stat().st_mode), 0o600)

    def test_rejects_duplicate_hostname_without_modifying_file(self):
        before = self.inventory_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "Hostname 'server1' already exists"):
            CLIENTS.add_client(self.inventory_path, "server1", "192.168.50.13")

        self.assertEqual(self.inventory_path.read_bytes(), before)

    def test_rejects_duplicate_ip_without_modifying_file(self):
        before = self.inventory_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "already assigned"):
            CLIENTS.add_client(self.inventory_path, "app-02", "192.168.50.11")

        self.assertEqual(self.inventory_path.read_bytes(), before)

    def test_rejects_monitoring_server_ip(self):
        before = self.inventory_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "already assigned"):
            CLIENTS.add_client(
                self.inventory_path, "app-03", "192.168.50.10"
            )

        self.assertEqual(self.inventory_path.read_bytes(), before)

    def test_rejects_invalid_hostname(self):
        before = self.inventory_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "Invalid hostname"):
            CLIENTS.add_client(self.inventory_path, "bad hostname", "192.168.50.13")

        self.assertEqual(self.inventory_path.read_bytes(), before)

    def test_rejects_invalid_or_non_ipv4_address(self):
        for address in ("not-an-ip", "2001:db8::1"):
            with self.subTest(address=address):
                before = self.inventory_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "IPv4"):
                    CLIENTS.add_client(self.inventory_path, "app-04", address)
                self.assertEqual(self.inventory_path.read_bytes(), before)

    def test_rejects_legacy_inventory_until_migrated(self):
        legacy = {
            "all": {
                "children": {
                    "production": {
                        "children": {
                            "frontend": {
                                "hosts": {
                                    "server1": {"ansible_host": "192.168.50.11"}
                                }
                            }
                        }
                    }
                }
            }
        }
        self.inventory_path.write_text(
            yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8"
        )
        before = self.inventory_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "no monitoring_clients group"):
            CLIENTS.add_client(self.inventory_path, "app-05", "192.168.50.13")

        self.assertEqual(self.inventory_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
