import unittest
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = ROOT_DIR / "roles/docker_engine/defaults/main.yml"
TASKS_PATH = ROOT_DIR / "roles/docker_engine/tasks/main.yml"
PLAYBOOK_PATH = ROOT_DIR / "playbooks/install-docker.yml"
QUICK_START_PATH = ROOT_DIR / "quick-start.sh"


class DockerEngineRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(
            DEFAULTS_PATH.read_text(encoding="utf-8")
        )
        cls.tasks = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))
        cls.playbook = yaml.safe_load(
            PLAYBOOK_PATH.read_text(encoding="utf-8")
        )
        cls.quick_start = QUICK_START_PATH.read_text(encoding="utf-8")

    def find_task(self, name):
        matches = [task for task in self.tasks if task.get("name") == name]
        self.assertEqual(len(matches), 1, f"Expected exactly one task: {name}")
        return matches[0]

    def test_uses_official_docker_repository_and_packages(self):
        self.assertEqual(
            self.defaults["docker_engine_apt_repositories"],
            {
                "Debian": "https://download.docker.com/linux/debian",
                "Ubuntu": "https://download.docker.com/linux/ubuntu",
            },
        )
        self.assertEqual(
            self.defaults["docker_engine_apt_key_urls"],
            {
                "Debian": "https://download.docker.com/linux/debian/gpg",
                "Ubuntu": "https://download.docker.com/linux/ubuntu/gpg",
            },
        )
        self.assertEqual(
            self.defaults["docker_engine_packages"],
            [
                "docker-ce",
                "docker-ce-cli",
                "containerd.io",
                "docker-buildx-plugin",
                "docker-compose-plugin",
            ],
        )
        self.assertIn("trixie", self.defaults["docker_engine_supported_releases"]["Debian"])
        self.assertIn("noble", self.defaults["docker_engine_supported_releases"]["Ubuntu"])
        self.assertIn("resolute", self.defaults["docker_engine_supported_releases"]["Ubuntu"])

    def test_validates_supported_os_release_before_installation(self):
        self.assertEqual(self.tasks[0]["name"], "Validate supported operating system")
        assertion = self.tasks[0]["ansible.builtin.assert"]
        conditions = assertion["that"]
        self.assertTrue(
            any("ansible_facts.distribution in docker_engine_supported_releases" in condition
                for condition in conditions)
        )
        self.assertTrue(
            any("ansible_facts.distribution_release" in condition
                and "docker_engine_supported_releases[ansible_facts.distribution]" in condition
                for condition in conditions)
        )

        selection_task = self.find_task(
            "Select official Docker APT repository for the operating system"
        )
        facts = selection_task["ansible.builtin.set_fact"]
        self.assertEqual(
            facts["docker_engine_apt_repository"],
            "{{ docker_engine_apt_repositories[ansible_facts.distribution] }}",
        )
        self.assertEqual(
            facts["docker_engine_apt_key_url"],
            "{{ docker_engine_apt_key_urls[ansible_facts.distribution] }}",
        )

    def test_detects_existing_docker_ce_or_docker_io(self):
        task = self.find_task("Detect an existing Docker Engine package")
        expression = task["ansible.builtin.set_fact"][
            "docker_engine_runtime_installed"
        ]
        self.assertIn("'docker-ce' in ansible_facts.packages", expression)
        self.assertIn("'docker.io' in ansible_facts.packages", expression)
        self.assertIn("or", expression)

    def test_skips_repository_setup_when_docker_runtime_exists(self):
        repository_task_names = [
            "Select official Docker APT repository for the operating system",
            "Refuse to replace potentially conflicting container packages automatically",
            "Ensure APT keyring directory exists",
            "Install Docker repository prerequisites",
            "Download official Docker repository signing key",
            "Configure official Docker APT repository",
            "Remove legacy Docker repository list",
            "Refresh APT cache for Docker repository",
            "Install Docker Engine and Compose plugin",
        ]

        for name in repository_task_names:
            with self.subTest(task=name):
                task = self.find_task(name)
                self.assertEqual(
                    task.get("when"),
                    "not docker_engine_runtime_installed",
                    f"{name} must be skipped when a Docker runtime already exists.",
                )

        for name in [
            "Ensure Docker service is enabled and running",
            "Verify Docker CLI",
            "Verify Docker Compose plugin",
        ]:
            with self.subTest(task=name):
                task = self.find_task(name)
                self.assertNotEqual(
                    task.get("when"),
                    "not docker_engine_runtime_installed",
                    f"{name} must still run when a Docker runtime already exists.",
                )

    def test_refuses_conflicting_packages_without_removing_them(self):
        task = self.find_task(
            "Refuse to replace potentially conflicting container packages automatically"
        )
        self.assertIn("ansible.builtin.assert", task)
        self.assertNotIn("docker.io", task["loop"])
        self.assertIn("containerd", task["loop"])
        self.assertIn("runc", task["loop"])

        for item in self.tasks:
            apt_task = item.get("ansible.builtin.apt", {})
            self.assertNotEqual(
                apt_task.get("state"),
                "absent",
                "The Docker role must not remove installed packages.",
            )

    def test_repository_uses_a_dedicated_signed_by_keyring(self):
        key_task = self.find_task("Download official Docker repository signing key")
        self.assertEqual(
            key_task["ansible.builtin.get_url"]["dest"],
            "{{ docker_engine_apt_keyring }}",
        )
        self.assertEqual(
            self.defaults["docker_engine_apt_keyring"],
            "/etc/apt/keyrings/docker.asc",
        )

        repo_task = self.find_task("Configure official Docker APT repository")
        content = repo_task["ansible.builtin.copy"]["content"]
        self.assertIn("URIs: {{ docker_engine_apt_repository }}", content)
        self.assertIn("Suites: {{ ansible_facts.distribution_release }}", content)
        self.assertIn("Signed-By: {{ docker_engine_apt_keyring }}", content)

    def test_installs_docker_starts_service_and_verifies_compose(self):
        install_task = self.find_task(
            "Install Docker Engine and Compose plugin"
        )
        self.assertEqual(
            install_task["ansible.builtin.apt"]["name"],
            "{{ docker_engine_packages }}",
        )
        self.assertEqual(
            install_task["when"],
            "not docker_engine_runtime_installed",
        )

        detection_task = self.find_task(
            "Detect an existing Docker Engine package"
        )
        self.assertIn(
            "docker-ce",
            detection_task["ansible.builtin.set_fact"][
                "docker_engine_runtime_installed"
            ],
        )
        self.assertIn(
            "docker.io",
            detection_task["ansible.builtin.set_fact"][
                "docker_engine_runtime_installed"
            ],
        )

        service_task = self.find_task(
            "Ensure Docker service is enabled and running"
        )
        self.assertEqual(
            service_task["ansible.builtin.service"]["state"],
            "started",
        )
        self.assertTrue(
            service_task["ansible.builtin.service"]["enabled"]
        )

        compose_task = self.find_task("Verify Docker Compose plugin")
        self.assertEqual(
            compose_task["ansible.builtin.command"]["argv"],
            ["docker", "compose", "version"],
        )

    def test_install_playbook_targets_monitoring_group_and_role(self):
        play = self.playbook[0]
        self.assertEqual(play["hosts"], "monitoring:monitoring_clients")
        self.assertTrue(play["become"])
        self.assertIn("docker_engine", play["roles"])

    def test_quick_start_reuses_successfully_deployed_single_profile(self):
        self.assertIn('STATE_FILE="$ROOT_DIR/.state/profile.json"', self.quick_start)
        self.assertIn('PROFILE_STATUS="$("$ROOT_DIR/scripts/profile-manager.sh" status)"', self.quick_start)
        self.assertIn("Active profile: single", self.quick_start)
        self.assertIn("Deployment status: deployed", self.quick_start)
        self.assertIn('MODE="single"', self.quick_start)
        self.assertIn(
            "Using saved profile: Single Instance (single/deployed)",
            self.quick_start,
        )

    def test_quick_start_keeps_explicit_modes_and_check_option(self):
        self.assertIn("    --mode)", self.quick_start)
        self.assertIn("    --check)", self.quick_start)
        self.assertIn('MODE="check"', self.quick_start)

    def test_quick_start_check_exits_before_deployment_playbooks(self):
        check_guard = self.quick_start.index(
            'if [[ "$MODE" == "check" ]]; then'
        )
        check_exit = self.quick_start.index("  exit 0", check_guard)
        deployment_start = self.quick_start.index(
            'info "Initializing or validating local profile state"'
        )

        self.assertLess(check_guard, check_exit)
        self.assertLess(check_exit, deployment_start)

        for playbook in (
            "playbooks/install-docker.yml",
            "playbooks/configure-monitoring.yml",
            "playbooks/deploy-monitoring.yml",
            "playbooks/preflight-monitoring-client.yml",
            "playbooks/install-log-generator.yml",
            "playbooks/configure-monitoring-client.yml",
            "playbooks/remove-log-generator.yml",
        ):
            self.assertGreater(
                self.quick_start.index(playbook, deployment_start),
                check_exit,
                f"{playbook} must remain after the check-mode exit",
            )

    def test_quick_start_defines_profile_menu_only_once(self):
        self.assertEqual(
            self.quick_start.count("Infrastructure Observability Stack"),
            1,
        )
        self.assertIn("show_profile_menu", self.quick_start)

    def test_quick_start_installs_docker_before_monitoring_deployment(self):
        deployment_start = self.quick_start.index(
            'info "Initializing or validating local profile state"'
        )
        deployment_sequence = self.quick_start[deployment_start:]
        docker_index = deployment_sequence.index(
            "playbooks/install-docker.yml"
        )
        configure_index = deployment_sequence.index(
            "playbooks/configure-monitoring.yml"
        )
        deploy_index = deployment_sequence.index(
            "playbooks/deploy-monitoring.yml"
        )
        self.assertLess(docker_index, configure_index)
        self.assertLess(configure_index, deploy_index)


if __name__ == "__main__":
    unittest.main()
