import unittest
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = ROOT_DIR / "roles/monitoring_agent/defaults/main.yml"
TASKS_PATH = ROOT_DIR / "roles/monitoring_agent/tasks/main.yml"
TEMPLATE_PATH = ROOT_DIR / "roles/monitoring_agent/templates/config.alloy.j2"


class CadvisorMonitoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(
            DEFAULTS_PATH.read_text(encoding="utf-8")
        )
        cls.tasks = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))
        cls.template = TEMPLATE_PATH.read_text(encoding="utf-8")

    def find_task(self, name):
        matches = [task for task in self.tasks if task.get("name") == name]
        self.assertEqual(
            len(matches), 1, f"Expected exactly one task: {name}"
        )
        return matches[0]

    def test_defines_pinned_cadvisor_image_and_internal_network(self):
        self.assertTrue(self.defaults.get("cadvisor_image"))
        self.assertNotIn(":latest", self.defaults["cadvisor_image"])
        self.assertTrue(self.defaults.get("cadvisor_container_name"))
        self.assertTrue(self.defaults.get("monitoring_agent_docker_network"))

    def test_creates_dedicated_internal_docker_network(self):
        task = self.find_task("Create internal monitoring Docker network")
        network = task["community.docker.docker_network"]
        self.assertEqual(
            network["name"], "{{ monitoring_agent_docker_network }}"
        )
        self.assertTrue(network["internal"])
        self.assertEqual(network["state"], "present")

    def test_runs_cadvisor_without_publishing_host_ports(self):
        task = self.find_task("Run cAdvisor container")
        container = task["community.docker.docker_container"]
        self.assertEqual(container["name"], "{{ cadvisor_container_name }}")
        self.assertEqual(container["state"], "started")
        self.assertEqual(container["restart_policy"], "unless-stopped")
        self.assertNotIn("published_ports", container)
        self.assertNotIn("ports", container)

        mounts = container.get("mounts", [])
        mount_sources = [mount.get("source") for mount in mounts]
        self.assertIn("/sys", mount_sources)
        self.assertIn("/", mount_sources)
        self.assertIn("/var/lib/docker", mount_sources)
        socket_mounts = [mount for mount in mounts if mount.get("source") == "/var/run/docker.sock"]
        self.assertEqual(len(socket_mounts), 1)
        self.assertEqual(socket_mounts[0].get("target"), "/var/run/docker.sock")
        self.assertTrue(socket_mounts[0].get("read_only"))

    def test_cadvisor_avoids_unneeded_disk_mount_and_privileged_mode(self):
        task = self.find_task("Run cAdvisor container")
        container = task["community.docker.docker_container"]
        mount_sources = [
            mount.get("source") for mount in container.get("mounts", [])
        ]

        self.assertNotIn("/dev/disk", mount_sources)
        self.assertFalse(container.get("privileged", False))
        self.assertEqual(container.get("cap_drop"), ["ALL"])

    def test_alloy_scrapes_cadvisor_and_keeps_existing_pipelines(self):
        self.assertIn('prometheus.scrape "cadvisor"', self.template)
        self.assertIn("cadvisor:8080", self.template)
        self.assertIn("prometheus.remote_write.central.receiver", self.template)
        self.assertIn('prometheus.remote_write "central"', self.template)
        self.assertIn('loki.write "central"', self.template)

    def test_host_metrics_use_inventory_hostname_as_instance_label(self):
        self.assertIn('discovery.relabel "host"', self.template)
        self.assertIn(
            'targets = prometheus.exporter.unix.host.targets',
            self.template,
        )
        self.assertIn(
            'target_label = "instance"\n    replacement  = "{{ alloy_hostname_label }}"',
            self.template,
        )
        self.assertIn(
            "targets = discovery.relabel.host.output",
            self.template,
        )

    def test_alloy_configuration_change_notifies_restart_handler(self):
        task = self.find_task("Install Alloy configuration")
        self.assertEqual(
            task.get("notify"),
            "Restart Alloy after configuration changes",
        )

        handlers_path = (
            ROOT_DIR / "roles/monitoring_agent/handlers/main.yml"
        )
        handlers = yaml.safe_load(handlers_path.read_text(encoding="utf-8"))
        matches = [
            handler for handler in handlers
            if handler.get("name") == "Restart Alloy after configuration changes"
        ]
        self.assertEqual(len(matches), 1)
        container = matches[0]["community.docker.docker_container"]
        self.assertEqual(container["name"], "{{ alloy_container_name }}")
        self.assertEqual(container["state"], "started")
        self.assertTrue(container["restart"])

    def test_alloy_template_does_not_mount_docker_socket(self):
        tasks_text = TASKS_PATH.read_text(encoding="utf-8")
        alloy_task_start = tasks_text.index("- name: Run Alloy monitoring container")
        alloy_task = tasks_text[alloy_task_start:]
        self.assertNotIn("/var/run/docker.sock", alloy_task)

    def test_cadvisor_uses_internal_network_and_alloy_keeps_central_connectivity(self):
        task = self.find_task("Run cAdvisor container")
        container = task["community.docker.docker_container"]
        networks = container.get("networks", [])
        self.assertTrue(
            any(
                network.get("name") == "{{ monitoring_agent_docker_network }}"
                for network in networks
            )
        )

        alloy_task = self.find_task("Run Alloy monitoring container")
        alloy_container = alloy_task["community.docker.docker_container"]
        alloy_networks = alloy_container.get("networks", [])
        self.assertTrue(
            any(network.get("name") == "bridge" for network in alloy_networks),
            "Alloy must retain an external route to central Prometheus and Loki.",
        )
        self.assertTrue(
            any(
                network.get("name") == "{{ monitoring_agent_docker_network }}"
                for network in alloy_networks
            )
        )


if __name__ == "__main__":
    unittest.main()
