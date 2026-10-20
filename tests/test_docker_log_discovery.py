import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
HELPER_PATH = (
    ROOT_DIR
    / "roles"
    / "monitoring_agent"
    / "files"
    / "discover_docker_log_targets.py"
)

spec = importlib.util.spec_from_file_location("docker_log_discovery", HELPER_PATH)
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


class DockerLogDiscoveryTests(unittest.TestCase):
    def test_discovers_application_logs_and_skips_monitoring_containers(self):
        with tempfile.NamedTemporaryFile() as app_log, \
             tempfile.NamedTemporaryFile() as alloy_log, \
             tempfile.NamedTemporaryFile() as proxy_log:

            containers = {
                "app-id": {
                    "Name": "/backend-app",
                    "Config": {"Image": "registry.example.com/apps/backend:v1"},
                    "HostConfig": {"LogConfig": {"Type": "json-file"}},
                    "LogPath": app_log.name,
                },
                "alloy-id": {
                    "Name": "/monitoring-alloy",
                    "Config": {"Image": "grafana/alloy:v1.20.1"},
                    "HostConfig": {"LogConfig": {"Type": "json-file"}},
                    "LogPath": alloy_log.name,
                },
                "legacy-alloy-id": {
                    "Name": "/clever_robinson",
                    "Config": {"Image": "grafana/alloy:v1.20.1"},
                    "HostConfig": {"LogConfig": {"Type": "json-file"}},
                    "LogPath": alloy_log.name,
                },
                "cadvisor-id": {
                    "Name": "/monitoring-cadvisor",
                    "Config": {"Image": "ghcr.io/google/cadvisor:v0.60.6"},
                    "HostConfig": {"LogConfig": {"Type": "json-file"}},
                    "LogPath": proxy_log.name,
                },
                "proxy-id": {
                    "Name": "/frontend-proxy",
                    "Config": {"Image": "openresty/openresty:alpine"},
                    "HostConfig": {"LogConfig": {"Type": "json-file"}},
                    "LogPath": proxy_log.name,
                },
            }

            rows = [
                {"ID": container_id, "Image": details["Config"]["Image"]}
                for container_id, details in containers.items()
            ]

            def fake_run(args, **kwargs):
                if args == ["docker", "ps", "--format", "json"]:
                    return subprocess.CompletedProcess(
                        args, 0, "\n".join(map(json.dumps, rows)), ""
                    )

                if args[:2] == ["docker", "inspect"]:
                    container_id = args[2]
                    return subprocess.CompletedProcess(
                        args, 0, json.dumps([containers[container_id]]), ""
                    )

                raise AssertionError(f"Unexpected command: {args}")

            stdout = io.StringIO()
            with patch.object(discovery.subprocess, "run", side_effect=fake_run):
                with patch.object(sys, "argv", ["discover_docker_log_targets.py"]):
                    with contextlib.redirect_stdout(stdout):
                        discovery.main()

            targets = json.loads(stdout.getvalue())
            self.assertEqual(
                [target["container"] for target in targets],
                ["backend-app", "frontend-proxy"],
            )
            self.assertEqual(len(targets), 2)
            self.assertTrue(
                all(target["path"].startswith("/host/root/") for target in targets)
            )

    def test_skips_containers_with_unsupported_logging_driver(self):
        with tempfile.NamedTemporaryFile() as app_log:
            containers = {
                "app-id": {
                    "Name": "/backend-app",
                    "Config": {"Image": "example/backend:v1"},
                    "HostConfig": {"LogConfig": {"Type": "json-file"}},
                    "LogPath": app_log.name,
                },
                "other-id": {
                    "Name": "/other-container",
                    "Config": {"Image": "example/other:v1"},
                    "HostConfig": {"LogConfig": {"Type": "journald"}},
                    "LogPath": "/var/log/other.log",
                },
            }

            rows = [
                {"ID": container_id, "Image": details["Config"]["Image"]}
                for container_id, details in containers.items()
            ]

            def fake_run(args, **kwargs):
                if args == ["docker", "ps", "--format", "json"]:
                    return subprocess.CompletedProcess(
                        args, 0, "\n".join(map(json.dumps, rows)), ""
                    )

                if args[:2] == ["docker", "inspect"]:
                    return subprocess.CompletedProcess(
                        args, 0, json.dumps([containers[args[2]]]), ""
                    )

                raise AssertionError(f"Unexpected command: {args}")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(discovery.subprocess, "run", side_effect=fake_run):
                with patch.object(sys, "argv", ["discover_docker_log_targets.py"]):
                    with contextlib.redirect_stdout(stdout), \
                         contextlib.redirect_stderr(stderr):
                        discovery.main()

            targets = json.loads(stdout.getvalue())
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["container"], "backend-app")
            self.assertIn("other-container", stderr.getvalue())

    def test_returns_empty_list_when_no_containers_are_running(self):
        def fake_run(args, **kwargs):
            if args == ["docker", "ps", "--format", "json"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            raise AssertionError(f"Unexpected command: {args}")

        stdout = io.StringIO()
        with patch.object(discovery.subprocess, "run", side_effect=fake_run):
            with patch.object(sys, "argv", ["discover_docker_log_targets.py"]):
                with contextlib.redirect_stdout(stdout):
                    discovery.main()

        self.assertEqual(json.loads(stdout.getvalue()), [])

    def test_role_discovers_logs_without_image_prefix(self):
        role_dir = HELPER_PATH.parents[1]
        tasks = (role_dir / "tasks" / "main.yml").read_text(encoding="utf-8")

        self.assertIn("Discover Docker log targets", tasks)
        self.assertIn(
            '"{{ alloy_base_dir }}/discover_docker_log_targets.py"',
            tasks,
        )
        self.assertNotIn(
            '"{{ alloy_application_image_prefix }}"',
            tasks,
        )
        self.assertNotIn(
            "when: alloy_application_image_prefix | trim | length > 0",
            tasks,
        )


if __name__ == "__main__":
    unittest.main()
