import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
QUICK_START = ROOT_DIR / "quick-start.sh"
MARKER_START = 'python - "$PROFILE_FILE" <<\'PYPROFILECHECK\''
MARKER_END = "\nPYPROFILECHECK"


class QuickStartGrafanaPortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = QUICK_START.read_text(encoding="utf-8")
        start = script.index(MARKER_START) + len(MARKER_START)
        end = script.index(MARKER_END, start)
        cls.validation_script = script[start:end].lstrip("\n")

    def validate_profile(self, profile):
        with tempfile.TemporaryDirectory(prefix="grafana-port-test-") as tmp:
            profile_path = Path(tmp) / "lab.yml"
            profile_path.write_text(
                yaml.safe_dump(profile, sort_keys=False),
                encoding="utf-8",
            )
            return subprocess.run(
                ["python", "-", str(profile_path)],
                input=self.validation_script,
                text=True,
                capture_output=True,
                check=False,
            )

    @staticmethod
    def profile_with_port(port_marker=True, port=3002):
        grafana = {"host_port": port} if port_marker else {}
        return {
            "lab": {
                "ssh": {
                    "auth_method": "password",
                    "become_password_required": False,
                },
                "grafana": grafana,
                "monitoring": {
                    "prometheus_remote_write_port": 9090,
                    "loki_push_port": 3100,
                },
            }
        }

    def test_accepts_valid_grafana_host_port(self):
        result = self.validate_profile(self.profile_with_port())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Lab profile validation: PASS", result.stdout)

    def test_rejects_legacy_profile_without_host_port_with_migration_message(self):
        result = self.validate_profile(
            self.profile_with_port(port_marker=False)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Migrate the existing lab profile", result.stderr)
        self.assertIn("lab.grafana.host_port", result.stderr)

    def test_rejects_out_of_range_host_port(self):
        result = self.validate_profile(self.profile_with_port(port=70000))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("between 1 and 65535", result.stderr)

    def test_rejects_boolean_host_port(self):
        result = self.validate_profile(self.profile_with_port(port=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("between 1 and 65535", result.stderr)

    def test_rejects_missing_monitoring_settings(self):
        profile = self.profile_with_port()
        del profile["lab"]["monitoring"]
        result = self.validate_profile(profile)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lab.monitoring settings", result.stderr)

    def test_rejects_invalid_prometheus_port(self):
        profile = self.profile_with_port()
        profile["lab"]["monitoring"]["prometheus_remote_write_port"] = 70000
        result = self.validate_profile(profile)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lab.monitoring.prometheus_remote_write_port", result.stderr)

    def test_rejects_boolean_loki_port(self):
        profile = self.profile_with_port()
        profile["lab"]["monitoring"]["loki_push_port"] = True
        result = self.validate_profile(profile)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lab.monitoring.loki_push_port", result.stderr)


if __name__ == "__main__":
    unittest.main()
