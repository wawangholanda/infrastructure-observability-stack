#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'USAGE'
Usage:
  ./quick-start.sh
  ./quick-start.sh --check
  ./quick-start.sh --mode single
  ./quick-start.sh --mode ha
  ./quick-start.sh --mode scale
  ./quick-start.sh --profile-status
  ./quick-start.sh --backup-profile

Profiles:
  single  Single Instance (monitoring server and client lab)
  ha      High Availability (planned; Proxmox lab testing required)
  scale   High Traffic / Scale-out (planned; Proxmox lab testing required)
  --check Check only; no deployment
USAGE
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '\n==> %s\n' "$*"
}

MODE="menu"
PROFILE_ACTION=""

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --profile-status|--backup-profile)
      [[ "$MODE" == "menu" && -z "$PROFILE_ACTION" ]] ||
        fail "Specify only one action."
      PROFILE_ACTION="$1"
      shift
      ;;
    --check)
      [[ "$MODE" == "menu" ]] || fail "Specify only one mode."
      MODE="check"
      shift
      ;;
    --mode)
      [[ "$MODE" == "menu" ]] || fail "Specify only one mode."
      [[ "$#" -ge 2 ]] || fail "--mode requires single, ha, or scale."
      case "$2" in
        single|ha|scale) MODE="$2" ;;
        *) fail "Unknown profile: $2" ;;
      esac
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "Unknown argument: $1"
      ;;
  esac
done

if [[ -n "$PROFILE_ACTION" ]]; then
  case "$PROFILE_ACTION" in
    --profile-status)
      exec "$ROOT_DIR/scripts/profile-manager.sh" status
      ;;
    --backup-profile)
      exec "$ROOT_DIR/scripts/profile-manager.sh" backup
      ;;
  esac
fi

show_profile_menu() {
  printf '\nInfrastructure Observability Stack\n'
  printf '%s\n' \
    '1) Single Instance (monitoring server + client lab)' \
    '2) High Availability (Proxmox lab; not enabled)' \
    '3) High Traffic / Scale-out (Proxmox lab; not enabled)' \
    '4) Check Only (no deployment)'
  printf '\nSelect profile [1-4]: '
  read -r choice

  case "$choice" in
    1) MODE="single" ;;
    2) MODE="ha" ;;
    3) MODE="scale" ;;
    4) MODE="check" ;;
    *) fail "Invalid selection. Choose 1, 2, 3, or 4." ;;
  esac
}

if [[ "$MODE" == "menu" ]]; then
  STATE_FILE="$ROOT_DIR/.state/profile.json"

  if [[ -e "$STATE_FILE" || -L "$STATE_FILE" ]]; then
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] ||
      fail "Profile state must be a regular, non-symlink file."
  fi

  # A saved deployment profile must never bypass explicit user selection.
  show_profile_menu
fi

case "$MODE" in
  ha)
    fail "High Availability is planned only. No changes were made. Implement and test it in the Proxmox lab first."
    ;;
  scale)
    fail "High Traffic / Scale-out is planned only. No changes were made. Implement and test it in the Proxmox lab first."
    ;;
  single)
    ;;
  check)
    ;;
esac

ENV_FILE="$ROOT_DIR/.secrets/.env"
INVENTORY_FILE="$ROOT_DIR/inventory/lab/hosts.yml"
LOCAL_BIND_VARS_FILE="$ROOT_DIR/inventory/local/group_vars/monitoring.yml"
PROFILE_FILE="$ROOT_DIR/.secrets/assets/lab.yml"

if [[ "$MODE" == "single" ]]; then
  command -v flock >/dev/null 2>&1 ||
    fail "Required command not found: flock"

  mkdir -p -- "$ROOT_DIR/.state"
  chmod 700 "$ROOT_DIR/.state"
  [[ ! -L "$ROOT_DIR/.state" ]] ||
    fail "State directory must not be a symlink."

  exec 9>"$ROOT_DIR/.state/deployment.lock"
  flock -n 9 ||
    fail "Another deployment appears to be running. No deployment was started."
fi

info "Checking local dependencies"
for cmd in ansible ansible-playbook ansible-inventory python ssh; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: $cmd"
done

# SSH host-key checking defaults to enabled; bypass only with explicit consent.
read -r -p "Bypass SSH host-key checking for this run? [y/N]: " BYPASS_HOST_KEY_CHECKING
case "$BYPASS_HOST_KEY_CHECKING" in
  y|Y|yes|YES|Yes)
    export ANSIBLE_HOST_KEY_CHECKING=False
    # Do not read or write known_hosts entries during this run.
    BYPASS_SSH_ARGS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null"
    export ANSIBLE_SSH_COMMON_ARGS="${BYPASS_SSH_ARGS}${ANSIBLE_SSH_COMMON_ARGS:+ ${ANSIBLE_SSH_COMMON_ARGS}}"
    printf '%s\n' "WARNING: SSH host identity verification is disabled for this run."
    printf '%s\n' "Use only on a trusted lab network; do not use this bypass in production."
    ;;
  *)
    export ANSIBLE_HOST_KEY_CHECKING=True
    info "SSH host-key checking remains enabled"
    ;;
esac


# Check-only must never bootstrap files or modify the workstation.
if [[ "$MODE" == "check" ]]; then
  info "Check-only: verifying existing local configuration"
  for file in \
    "$ROOT_DIR/.secrets/assets/lab.yml" \
    "$INVENTORY_FILE" \
    "$LOCAL_BIND_VARS_FILE" \
    "$ENV_FILE"
  do
    [[ -s "$file" ]] || fail "Check-only requires an existing non-empty file: ${file#"$ROOT_DIR/"}"
  done
  info "Check-only: skipping bootstrap and workstation changes"
else
BOOTSTRAP_HELPER="$ROOT_DIR/scripts/bootstrap-lab.py"

[[ -s "$BOOTSTRAP_HELPER" ]] ||
  fail "Required file is missing or empty: scripts/bootstrap-lab.py"

info "Checking lab configuration"
PORTS_ALREADY_CONFIGURED=false
if [[ -s "$ROOT_DIR/.secrets/assets/lab.yml" &&
      -s "$INVENTORY_FILE" &&
      -s "$LOCAL_BIND_VARS_FILE" ]]; then
  PORTS_ALREADY_CONFIGURED=true
fi

python "$BOOTSTRAP_HELPER" --config-dir "$ROOT_DIR"

if [[ "$PORTS_ALREADY_CONFIGURED" == true ]]; then
  PORT_CONFIG_HELPER="$ROOT_DIR/scripts/configure-monitoring-ports.py"
  [[ -s "$PORT_CONFIG_HELPER" ]] ||
    fail "Required file is missing or empty: scripts/configure-monitoring-ports.py"
  info "Configuring monitoring host ports"
  python "$PORT_CONFIG_HELPER" --config-dir "$ROOT_DIR"
fi

# Automated workstation support is intentionally limited to these platforms.
WORKSTATION_SUPPORTED=false
case "$(uname -s)" in
  Darwin)
    WORKSTATION_SUPPORTED=true
    ;;
  Linux)
    if [[ -r /etc/os-release ]]; then
      # shellcheck disable=SC1091
      . /etc/os-release
      case "${ID:-}" in
        ubuntu|debian|kali) WORKSTATION_SUPPORTED=true ;;
      esac
    fi
    ;;
esac

if [[ "$WORKSTATION_SUPPORTED" == true ]]; then
  info "Inspecting supported workstation platform"
  [[ -s "$ROOT_DIR/playbooks/workstation.yml" ]] || fail "Required file is missing or empty: playbooks/workstation.yml"
  # Keep workstation checks isolated from remote lab inventory variables.
  ansible-playbook -i 'localhost,' -c local "$ROOT_DIR/playbooks/workstation.yml" --syntax-check
  ansible-playbook -i 'localhost,' -c local "$ROOT_DIR/playbooks/workstation.yml"
else
  info "Skipping workstation playbook on unsupported operating system"
  printf '%s\n' \
    "Supported automated workstation platforms: Ubuntu, Debian, Kali Linux, and macOS." \
    "Install Python 3, Ansible Core, Git, and the OpenSSH client manually." \
    "Check Only can continue if those dependencies are already available."
fi

fi

info "Checking required project files"
for file in \
  "$INVENTORY_FILE" \
  "$ENV_FILE" \
  "$LOCAL_BIND_VARS_FILE" \
  "$ROOT_DIR/playbooks/preflight.yml" \
  "$ROOT_DIR/playbooks/install-docker.yml" \
  "$ROOT_DIR/playbooks/preflight-monitoring-client.yml" \
  "$ROOT_DIR/playbooks/install-log-generator.yml" \
  "$ROOT_DIR/playbooks/remove-log-generator.yml" \
  "$ROOT_DIR/playbooks/configure-monitoring-client.yml" \
  "$ROOT_DIR/playbooks/configure-monitoring.yml" \
  "$ROOT_DIR/files/monitoring/docker-compose.yml" \
  "$ROOT_DIR/files/monitoring/prometheus/prometheus.yml" \
  "$ROOT_DIR/files/monitoring/loki/config.yml" \
  "$ROOT_DIR/files/monitoring/grafana/provisioning/datasources/datasources.yml" \
  "$ROOT_DIR/files/monitoring/grafana/provisioning/dashboards/dashboards.yml"
do
  [[ -s "$file" ]] || fail "Required file is missing or empty: ${file#"$ROOT_DIR/"}"
done

info "Validating lab profile structure"
python - "$PROFILE_FILE" <<'PYPROFILECHECK'
import sys
from pathlib import Path
import yaml

try:
    profile = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, yaml.YAMLError):
    print("ERROR: Lab profile is unreadable or contains invalid YAML.", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(profile, dict):
    print("ERROR: Lab profile must contain a YAML mapping.", file=sys.stderr)
    raise SystemExit(1)

lab = profile.get("lab")
ssh = lab.get("ssh") if isinstance(lab, dict) else None

if not isinstance(ssh, dict):
    print("ERROR: Lab profile must define lab.ssh settings.", file=sys.stderr)
    raise SystemExit(1)

if ssh.get("auth_method") not in {"key", "password"}:
    print("ERROR: lab.ssh.auth_method must be 'key' or 'password'.", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(ssh.get("become_password_required"), bool):
    print("ERROR: lab.ssh.become_password_required must be true or false.", file=sys.stderr)
    raise SystemExit(1)

grafana = lab.get("grafana")
if not isinstance(grafana, dict):
    print(
        "ERROR: Lab profile is missing lab.grafana settings. "
        "Migrate the existing lab profile to include lab.grafana.host_port.",
        file=sys.stderr,
    )
    raise SystemExit(1)

host_port = grafana.get("host_port")
if isinstance(host_port, bool) or not isinstance(host_port, int) or not 1 <= host_port <= 65535:
    print(
        "ERROR: lab.grafana.host_port must be an integer between 1 and 65535. "
        "Migrate the existing lab profile and set the Grafana host port "
        "(for example, 3002).",
        file=sys.stderr,
    )
    raise SystemExit(1)

monitoring = lab.get("monitoring")
if not isinstance(monitoring, dict):
    print(
        "ERROR: Lab profile must define lab.monitoring settings, including "
        "prometheus_remote_write_port and loki_push_port.",
        file=sys.stderr,
    )
    raise SystemExit(1)

for key in ("prometheus_remote_write_port", "loki_push_port"):
    port = monitoring.get(key)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        print(
            f"ERROR: lab.monitoring.{key} must be an integer between 1 and 65535.",
            file=sys.stderr,
        )
        raise SystemExit(1)

print("Lab profile validation: PASS")
PYPROFILECHECK

info "Checking secret file permissions and required values"
python - "$ENV_FILE" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.stat().st_mode & 0o777 != 0o600:
    print("ERROR: .secrets/.env must have permission 600", file=sys.stderr)
    raise SystemExit(1)

required = {
    "GRAFANA_ADMIN_USER",
    "GRAFANA_ADMIN_PASSWORD",
    "GRAFANA_SECRET_KEY",
}
values = {}

for raw_line in path.read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key in required:
        if key in values:
            print(f"ERROR: Duplicate key: {key}", file=sys.stderr)
            raise SystemExit(1)
        values[key] = value.strip()

missing = sorted(required - values.keys())
invalid = sorted(
    key for key in required & values.keys()
    if not values[key] or "CHANGE_ME" in values[key]
)

if missing:
    print("ERROR: Missing required keys:", ", ".join(missing), file=sys.stderr)
if invalid:
    print("ERROR: Empty or placeholder values:", ", ".join(invalid), file=sys.stderr)
if missing or invalid:
    raise SystemExit(1)

print("Secret validation: PASS (values not displayed)")
PY

info "Checking inventory"
ansible-inventory -i "$INVENTORY_FILE" --graph >/dev/null
ansible -i "$INVENTORY_FILE" monitoring --list-hosts
ansible -i "$INVENTORY_FILE" monitoring_clients --list-hosts

info "Checking playbook syntax"
for playbook in "$ROOT_DIR"/playbooks/*.yml; do
  [[ -f "$playbook" ]] || continue
  case "$(basename "$playbook")" in
    workstation.yml)
      ansible-playbook -i 'localhost,' -c local "$playbook" --syntax-check
      ;;
    configure-monitoring.yml)
      ansible-playbook -i "$INVENTORY_FILE" -e "@$LOCAL_BIND_VARS_FILE" "$playbook" --syntax-check
      ;;
    *)
      ansible-playbook -i "$INVENTORY_FILE" "$playbook" --syntax-check
      ;;
  esac
done

info "Preparing temporary SSH/become authentication files"
AUTH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/monitoring-observability-auth.XXXXXX")"
chmod 700 "$AUTH_DIR"
cleanup_auth() {
  rm -rf -- "$AUTH_DIR"
}
trap cleanup_auth EXIT HUP INT TERM

mapfile -t SSH_SETTINGS < <(
  python - "$PROFILE_FILE" <<'PYPROFILE'
import sys
from pathlib import Path
import yaml

profile = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
ssh = profile.get("lab", {}).get("ssh", {})
auth_method = ssh.get("auth_method")
if auth_method not in {"key", "password"}:
    raise SystemExit("Invalid lab SSH authentication method.")
if not isinstance(ssh.get("become_password_required"), bool):
    raise SystemExit("Invalid lab become_password_required setting.")
print(auth_method)
print(str(ssh["become_password_required"]).lower())
PYPROFILE
) || fail "Could not read SSH settings from the lab profile."

SSH_AUTH_METHOD="${SSH_SETTINGS[0]:-}"
BECOME_PASSWORD_REQUIRED="${SSH_SETTINGS[1]:-}"
[[ "$SSH_AUTH_METHOD" == "key" || "$SSH_AUTH_METHOD" == "password" ]] ||
  fail "Invalid SSH authentication method in lab profile."
[[ "$BECOME_PASSWORD_REQUIRED" == "true" || "$BECOME_PASSWORD_REQUIRED" == "false" ]] ||
  fail "Invalid become password setting in lab profile."

AUTH_ARGS=(--timeout 30)

if [[ "$SSH_AUTH_METHOD" == "password" ]]; then
  if [[ "$BECOME_PASSWORD_REQUIRED" == "true" ]]; then
    read -r -s -p "Enter the lab user password (used for SSH and sudo/become): " LAB_PASSWORD
  else
    read -r -s -p "Enter the SSH password for the configured lab user: " LAB_PASSWORD
  fi
  printf '\n'
  [[ -n "$LAB_PASSWORD" ]] || fail "Password must not be empty."

  umask 077
  printf '%s' "$LAB_PASSWORD" > "$AUTH_DIR/lab-password"
  unset LAB_PASSWORD
  chmod 600 "$AUTH_DIR/lab-password"

  AUTH_ARGS+=(--connection-password-file "$AUTH_DIR/lab-password")
  if [[ "$BECOME_PASSWORD_REQUIRED" == "true" ]]; then
    AUTH_ARGS+=(--become-password-file "$AUTH_DIR/lab-password")
  fi
else
  info "Using the private key configured in the lab inventory"

  if [[ "$BECOME_PASSWORD_REQUIRED" == "true" ]]; then
    read -r -s -p "Enter the sudo/become password for the configured lab user: " BECOME_PASSWORD
    printf '\n'
    [[ -n "$BECOME_PASSWORD" ]] || fail "Sudo/become password must not be empty."

    umask 077
    printf '%s' "$BECOME_PASSWORD" > "$AUTH_DIR/become-password"
    unset BECOME_PASSWORD
    chmod 600 "$AUTH_DIR/become-password"
    AUTH_ARGS+=(--become-password-file "$AUTH_DIR/become-password")
  else
    info "Using passwordless sudo/become as configured"
  fi
fi

info "Checking SSH connectivity"
ansible -i "$INVENTORY_FILE" "monitoring,monitoring_clients" -m ansible.builtin.ping "${AUTH_ARGS[@]}"

info "Running read-only preflight"
ansible-playbook -i "$INVENTORY_FILE" playbooks/preflight.yml "${AUTH_ARGS[@]}"

if [[ "$MODE" == "check" ]]; then
  printf '\nChecks passed. No monitoring deployment was run.\n'
  exit 0
fi

if [[ "$MODE" == "single" ]]; then
  while true; do
    printf '\nAdd a new monitoring client? [y/N]: '
    read -r add_client_choice

    case "${add_client_choice,,}" in
      y|yes)
        while true; do
          printf 'Inventory hostname for the new client: '
          read -r new_client_name
          printf 'IPv4 address for the new client: '
          read -r new_client_ip

          if python "$ROOT_DIR/scripts/manage_monitoring_clients.py" \
            --inventory "$INVENTORY_FILE" \
            --name "$new_client_name" \
            --ip "$new_client_ip"; then
            info "Monitoring client added to inventory"
            break
          fi

          printf '\nClient was not added. Check the hostname/IP and try again.\n'
        done
        break
        ;;
      n|no|"")
        info "Keeping the existing monitoring client inventory"
        break
        ;;
      *)
        printf 'Invalid choice. Enter yes or no.\n'
        ;;
    esac
  done

  info "Checking SSH connectivity for all monitoring hosts"
  ansible -i "$INVENTORY_FILE" "monitoring,monitoring_clients" -m ansible.builtin.ping "${AUTH_ARGS[@]}"
fi

info "Initializing or validating local profile state"
"$ROOT_DIR/scripts/profile-manager.sh" init

info "Installing Docker Engine on monitoring hosts"
ansible-playbook -i "$INVENTORY_FILE" playbooks/install-docker.yml "${AUTH_ARGS[@]}"

info "Configuring monitoring stack files"
ansible-playbook -i "$INVENTORY_FILE" -e "@$LOCAL_BIND_VARS_FILE" playbooks/configure-monitoring.yml "${AUTH_ARGS[@]}"

info "Deploying monitoring stack"
ansible-playbook -i "$INVENTORY_FILE" playbooks/deploy-monitoring.yml "${AUTH_ARGS[@]}"

info "Checking client-to-monitoring connectivity"
ansible-playbook -i "$INVENTORY_FILE" playbooks/preflight-monitoring-client.yml "${AUTH_ARGS[@]}"

info "Installing stdout log generator on monitoring clients"
ansible-playbook -i "$INVENTORY_FILE" playbooks/install-log-generator.yml "${AUTH_ARGS[@]}"

info "Configuring monitoring agent on monitoring clients"
ansible-playbook -i "$INVENTORY_FILE" playbooks/configure-monitoring-client.yml "${AUTH_ARGS[@]}"

while true; do
  printf '\nHave you finished checking logs in Grafana/Loki? Remove the log-generator container now? [yes/no]: '
  read -r cleanup_choice
  case "${cleanup_choice,,}" in
    yes|y)
      info "Removing log generator from monitoring clients"
      ansible-playbook -i "$INVENTORY_FILE" playbooks/remove-log-generator.yml "${AUTH_ARGS[@]}"
      break
      ;;
    no|n)
      info "Keeping log generator running on monitoring clients"
      break
      ;;
    *)
      printf 'Invalid choice. Enter yes or no.\n'
      ;;
  esac
done

info "Recording successful Single Instance deployment"
"$ROOT_DIR/scripts/profile-manager.sh" set-state single deployed

printf '\nSingle Instance deployment completed successfully.\n'
