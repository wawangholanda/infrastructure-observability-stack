import json
import unittest
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = (
    ROOT_DIR
    / "files/monitoring/grafana/dashboards/container-resource-overview.json"
)
PROVISIONING_PATH = (
    ROOT_DIR
    / "files/monitoring/grafana/provisioning/dashboards/dashboards.yml"
)
DATASOURCES_PATH = (
    ROOT_DIR
    / "files/monitoring/grafana/provisioning/datasources/datasources.yml"
)
COMPOSE_PATH = ROOT_DIR / "files/monitoring/docker-compose.yml"
STACK_TASKS_PATH = ROOT_DIR / "roles/monitoring_stack/tasks/main.yml"


class GrafanaDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = json.loads(
            DASHBOARD_PATH.read_text(encoding="utf-8")
        )
        cls.provisioning = yaml.safe_load(
            PROVISIONING_PATH.read_text(encoding="utf-8")
        )
        cls.datasources = yaml.safe_load(
            DATASOURCES_PATH.read_text(encoding="utf-8")
        )
        cls.compose = yaml.safe_load(
            COMPOSE_PATH.read_text(encoding="utf-8")
        )

    def test_dashboard_identity_and_panel_count(self):
        self.assertEqual(
            self.dashboard.get("title"), "Container Resource Overview"
        )
        self.assertEqual(
            self.dashboard.get("uid"), "container-resource-overview"
        )
        self.assertEqual(len(self.dashboard.get("panels", [])), 7)

    def test_dashboard_panels_have_expected_queries(self):
        panels = {
            panel.get("title"): panel
            for panel in self.dashboard["panels"]
        }
        expected_titles = {
            "Container CPU Usage",
            "Container Memory Working Set",
            "Top 10 Containers by CPU",
            "Top 10 Containers by Memory",
            "Host Disk Usage (%)",
            "Host Disk Used",
            "Host Disk Available",
        }
        self.assertEqual(set(panels), expected_titles)

        for title, panel in panels.items():
            with self.subTest(panel=title):
                targets = panel.get("targets", [])
                self.assertTrue(targets, f"{title} has no query targets")
                for target in targets:
                    expression = target.get("expr", "")
                    self.assertTrue(expression, f"{title} has an empty query")
                    self.assertIn('instance=~"$host"', expression)
                    if title in {
                        "Container CPU Usage",
                        "Container Memory Working Set",
                        "Top 10 Containers by CPU",
                        "Top 10 Containers by Memory",
                    }:
                        self.assertIn('job="cadvisor"', expression)
                        self.assertIn("container_", expression)
                        self.assertIn('name=~"$container"', expression)

    def test_host_disk_panels_use_node_exporter_metrics_and_host_filter(self):
        panels = {panel.get("title"): panel for panel in self.dashboard["panels"]}
        expected = {
            "Host Disk Usage (%)": "node_filesystem_avail_bytes",
            "Host Disk Used": "node_filesystem_size_bytes",
            "Host Disk Available": "node_filesystem_avail_bytes",
        }
        for title, metric in expected.items():
            with self.subTest(panel=title):
                panel = panels[title]
                self.assertEqual(panel["datasource"]["uid"], "prometheus")
                expression = panel["targets"][0]["expr"]
                self.assertIn(metric, expression)
                self.assertIn('instance=~"$host"', expression)
                self.assertIn("fstype!~", expression)

    def test_dashboard_variables_use_prometheus(self):
        variables = {
            item.get("name"): item
            for item in self.dashboard.get("templating", {}).get("list", [])
        }
        self.assertIn("host", variables)
        self.assertIn("container", variables)

        for name in ("host", "container"):
            with self.subTest(variable=name):
                datasource = variables[name].get("datasource", {})
                self.assertEqual(datasource.get("type"), "prometheus")
                self.assertEqual(datasource.get("uid"), "prometheus")

    def test_provisioning_discovers_dashboard_directory(self):
        providers = self.provisioning.get("providers", [])
        matches = [
            provider for provider in providers
            if provider.get("name") == "Repository Dashboards"
        ]
        self.assertEqual(len(matches), 1)

        provider = matches[0]
        self.assertEqual(provider.get("type"), "file")
        self.assertEqual(
            provider.get("options", {}).get("path"),
            "/var/lib/grafana/dashboards",
        )

    def test_compose_mounts_dashboard_directory_read_only(self):
        grafana = self.compose["services"]["grafana"]
        mounts = grafana.get("volumes", [])
        self.assertIn(
            "./grafana/dashboards:/var/lib/grafana/dashboards:ro",
            mounts,
        )
        self.assertIn(
            "./grafana/provisioning:/etc/grafana/provisioning:ro",
            mounts,
        )

    def test_prometheus_datasource_uid_matches_dashboard(self):
        datasources = self.datasources.get("datasources", [])
        prometheus = [
            item for item in datasources
            if item.get("name") == "Prometheus"
        ]
        self.assertEqual(len(prometheus), 1)
        self.assertEqual(prometheus[0].get("uid"), "prometheus")
        self.assertEqual(prometheus[0].get("type"), "prometheus")

    def test_logs_dashboard_identity_panels_and_loki_queries(self):
        dashboard_path = (
            ROOT_DIR
            / "files/monitoring/grafana/dashboards/container-logs-explorer.json"
        )
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        self.assertEqual(dashboard.get("title"), "Container Logs Explorer")
        self.assertEqual(dashboard.get("uid"), "container-logs-explorer")
        self.assertEqual(len(dashboard.get("panels", [])), 6)

        panels = {
            panel.get("title"): panel
            for panel in dashboard.get("panels", [])
        }
        self.assertEqual(
            set(panels),
            {
                "Error Log Lines",
                "Warning Log Lines",
                "Log Lines by Container",
                "Container Logs",
                "Top 10 HTTP Paths by Hits",
                "Slow HTTP Requests (>300 ms)",
            },
        )

        for title, panel in panels.items():
            with self.subTest(panel=title):
                self.assertEqual(panel["datasource"].get("uid"), "loki")
                targets = panel.get("targets", [])
                self.assertTrue(targets, f"{title} has no query targets")
                for target in targets:
                    expression = target.get("expr", "")
                    self.assertIn('job="docker"', expression)
                    self.assertIn('instance=~"$host"', expression)
                    self.assertIn('container=~"$container"', expression)

        self.assertIn(
            '(?i)error',
            panels["Error Log Lines"]["targets"][0]["expr"],
        )
        self.assertIn(
            '(?i)warn',
            panels["Warning Log Lines"]["targets"][0]["expr"],
        )
        self.assertIn(
            '|= "$search"',
            panels["Container Logs"]["targets"][0]["expr"],
        )

        slow_requests = panels["Slow HTTP Requests (>300 ms)"]
        self.assertEqual(slow_requests.get("type"), "logs")
        self.assertEqual(slow_requests.get("gridPos", {}).get("y"), 36)
        slow_query = slow_requests["targets"][0]["expr"]
        self.assertEqual(slow_requests["targets"][0].get("queryType"), "range")
        self.assertIn("RequestDuration > 300", slow_query)
        self.assertIn('msg="REQUEST_LOG"', slow_query)
        self.assertIn("| json", slow_query)
        self.assertIn("| line_format", slow_query)
        self.assertIn("{{.RequestDuration}}", slow_query)
        self.assertIn("{{.StatusCode}}", slow_query)
        self.assertIn("{{.URL}}", slow_query)

        top_paths = panels["Top 10 HTTP Paths by Hits"]
        self.assertEqual(top_paths.get("type"), "bargauge")
        self.assertEqual(top_paths.get("datasource", {}).get("uid"), "loki")
        self.assertEqual(top_paths.get("targets", [{}])[0].get("queryType"), "range")
        transformations = top_paths.get("transformations", [])
        self.assertEqual(
            [item.get("id") for item in transformations],
            ["reduce", "sortBy"],
        )
        self.assertEqual(
            transformations[0].get("options", {}).get("mode"),
            "seriesToRows",
        )
        self.assertEqual(
            transformations[0].get("options", {}).get("reducers"),
            ["lastNotNull"],
        )
        sort_options = transformations[1].get("options", {})
        self.assertEqual(
            sort_options.get("sort"),
            [{"field": "Last *", "desc": True}],
        )
        top_paths_query = top_paths["targets"][0]["expr"]
        self.assertIn("topk(10,", top_paths_query)
        self.assertIn("sum by (path)", top_paths_query)
        self.assertIn("| json", top_paths_query)
        self.assertIn('msg="REQUEST_LOG"', top_paths_query)
        self.assertIn('URL != ""', top_paths_query)
        self.assertIn('__error__!="JSONParserErr"', top_paths_query)
        self.assertIn("label_format path=", top_paths_query)
        self.assertIn("regexReplaceAll", top_paths_query)
        self.assertIn(r'\\?.*$', top_paths_query)
        self.assertIn('"^[^/]+"', top_paths_query)
        self.assertIn('instance=~"$host"', top_paths_query)
        self.assertIn('container=~"$container"', top_paths_query)
        self.assertNotIn("container_name_actual", top_paths_query)
        self.assertEqual(top_paths.get("gridPos", {}).get("x"), 0)

    def test_logs_dashboard_variables_use_loki(self):
        dashboard_path = (
            ROOT_DIR
            / "files/monitoring/grafana/dashboards/container-logs-explorer.json"
        )
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
        variables = {
            item.get("name"): item
            for item in dashboard.get("templating", {}).get("list", [])
        }
        self.assertTrue({"host", "container", "search"} <= variables.keys())

        for name in ("host", "container"):
            with self.subTest(variable=name):
                self.assertEqual(
                    variables[name].get("datasource", {}).get("uid"), "loki"
                )

    def test_ansible_installs_logs_dashboard_with_readable_permissions(self):
        tasks = yaml.safe_load(
            STACK_TASKS_PATH.read_text(encoding="utf-8")
        )
        matches = [
            task for task in tasks
            if task.get("name") == "Install Grafana container logs dashboard"
        ]
        self.assertEqual(len(matches), 1)

        copy = matches[0]["ansible.builtin.copy"]
        self.assertEqual(
            copy.get("src"),
            "{{ playbook_dir }}/../files/monitoring/grafana/dashboards/container-logs-explorer.json",
        )
        self.assertEqual(
            copy.get("dest"),
            "{{ monitoring_base_dir }}/grafana/dashboards/container-logs-explorer.json",
        )
        self.assertEqual(copy.get("owner"), "root")
        self.assertEqual(copy.get("group"), "root")
        self.assertEqual(copy.get("mode"), "0644")

    def test_ansible_installs_dashboard_with_readable_permissions(self):
        tasks = yaml.safe_load(
            STACK_TASKS_PATH.read_text(encoding="utf-8")
        )
        matches = [
            task for task in tasks
            if task.get("name") == "Install Grafana container resource dashboard"
        ]
        self.assertEqual(len(matches), 1)

        copy = matches[0]["ansible.builtin.copy"]
        self.assertEqual(
            copy.get("src"),
            "{{ playbook_dir }}/../files/monitoring/grafana/dashboards/container-resource-overview.json",
        )
        self.assertEqual(
            copy.get("dest"),
            "{{ monitoring_base_dir }}/grafana/dashboards/container-resource-overview.json",
        )
        self.assertEqual(copy.get("owner"), "root")
        self.assertEqual(copy.get("group"), "root")
        self.assertEqual(copy.get("mode"), "0644")


if __name__ == "__main__":
    unittest.main()
