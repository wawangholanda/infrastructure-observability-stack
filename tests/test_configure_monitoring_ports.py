import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "configure-monitoring-ports.py"
SPEC = importlib.util.spec_from_file_location("configure_monitoring_ports", SCRIPT)
PORTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PORTS)


class ConfigureMonitoringPortsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="monitoring-ports-test-")
        self.root = Path(self.temp.name)

        self.profile_path = self.root / ".secrets/assets/lab.yml"
        self.inventory_path = self.root / "inventory/lab/hosts.yml"
        self.bind_path = self.root / "inventory/local/group_vars/monitoring.yml"

        self.write_yaml(
            self.profile_path,
            {
                "lab": {
                    "grafana": {"host_port": 3002, "lan_bind_ip": "10.8.0.20"},
                    "monitoring": {
                        "prometheus_remote_write_port": 9090,
                        "loki_push_port": 3100,
                        "endpoint_host": "10.8.0.20",
                    },
                    "ssh": {"auth_method": "key"},
                }
            },
        )
        self.write_yaml(
            self.inventory_path,
            {
                "all": {
                    "vars": {
                        "ansible_user": "ubuntu",
                        "prometheus_remote_write_port": 9090,
                        "loki_push_port": 3100,
                    }
                }
            },
        )
        self.write_yaml(
            self.bind_path,
            {
                "grafana_host_port": 3002,
                "monitoring_listen_ip": "10.8.0.20",
                "custom_setting": "preserve-me",
            },
        )

        for path in (self.profile_path, self.inventory_path, self.bind_path):
            path.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write_yaml(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def load_yaml(self, path):
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_saved_ports_are_defaults_and_files_remain_consistent(self):
        with patch("builtins.input", side_effect=["", "", ""]) as mocked_input:
            PORTS.configure(self.root)

        self.assertEqual(
            [call.args[0] for call in mocked_input.call_args_list],
            [
                "Grafana Host Port [3002]: ",
                "Prometheus Host Port [9090]: ",
                "Loki Host Port [3100]: ",
            ],
        )

        profile = self.load_yaml(self.profile_path)
        inventory = self.load_yaml(self.inventory_path)
        bind = self.load_yaml(self.bind_path)

        self.assertEqual(profile["lab"]["grafana"]["host_port"], 3002)
        self.assertEqual(
            profile["lab"]["monitoring"]["prometheus_remote_write_port"], 9090
        )
        self.assertEqual(profile["lab"]["monitoring"]["loki_push_port"], 3100)
        self.assertEqual(inventory["all"]["vars"]["prometheus_remote_write_port"], 9090)
        self.assertEqual(inventory["all"]["vars"]["loki_push_port"], 3100)
        self.assertEqual(bind["grafana_host_port"], 3002)
        self.assertEqual(bind["prometheus_host_port"], 9090)
        self.assertEqual(bind["loki_host_port"], 3100)
        self.assertEqual(bind["custom_setting"], "preserve-me")

    def test_custom_ports_are_saved_consistently_with_secure_permissions(self):
        with patch("builtins.input", side_effect=["3005", "9095", "3105"]):
            PORTS.configure(self.root)

        profile = self.load_yaml(self.profile_path)
        inventory = self.load_yaml(self.inventory_path)
        bind = self.load_yaml(self.bind_path)

        self.assertEqual(profile["lab"]["grafana"]["host_port"], 3005)
        self.assertEqual(
            profile["lab"]["monitoring"]["prometheus_remote_write_port"], 9095
        )
        self.assertEqual(profile["lab"]["monitoring"]["loki_push_port"], 3105)
        self.assertEqual(inventory["all"]["vars"]["prometheus_remote_write_port"], 9095)
        self.assertEqual(inventory["all"]["vars"]["loki_push_port"], 3105)
        self.assertEqual(
            {key: bind[key] for key in (
                "grafana_host_port", "prometheus_host_port", "loki_host_port"
            )},
            {
                "grafana_host_port": 3005,
                "prometheus_host_port": 9095,
                "loki_host_port": 3105,
            },
        )

        for path in (self.profile_path, self.inventory_path, self.bind_path):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_invalid_port_input_is_rejected_before_accepting_valid_port(self):
        with patch("builtins.input", side_effect=["bad", "70000", "3008", "9098", "3108"]):
            with patch("sys.stderr"):
                PORTS.configure(self.root)

        profile = self.load_yaml(self.profile_path)
        self.assertEqual(profile["lab"]["grafana"]["host_port"], 3008)
        self.assertEqual(
            profile["lab"]["monitoring"]["prometheus_remote_write_port"], 9098
        )
        self.assertEqual(profile["lab"]["monitoring"]["loki_push_port"], 3108)


if __name__ == "__main__":
    unittest.main()
