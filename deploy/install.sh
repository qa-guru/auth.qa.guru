#!/usr/bin/env bash
# Copy realm + compose to the IdP VM, install root-owned env, create DB role,
# start Keycloak (start --optimized --import-realm). Run from the laptop.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WRAPPER="$(cd "${ROOT}/.." && pwd)"
REALM_SRC="${WRAPPER}/dev/realm/qaguru-realm.json"
HOST="${AUTH_DEPLOY_HOST:-auth-qa-guru}"
REMOTE="/opt/auth.qa.guru"
ENV_FILE="${AUTH_ENV:-${HOME}/.config/auth-qa-guru/keycloak.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "FAIL: ${ENV_FILE} missing — run ./deploy/init-env.sh" >&2
  exit 1
fi
if [[ ! -f "${REALM_SRC}" ]]; then
  echo "FAIL: realm SSOT missing: ${REALM_SRC}" >&2
  exit 1
fi

install -d -m 755 "${ROOT}/realm"
cp "${REALM_SRC}" "${ROOT}/realm/qaguru-realm.json"

ssh "${HOST}" "sudo mkdir -p '${REMOTE}' /etc/keycloak /etc/systemd/system && sudo chown qaguru:qaguru '${REMOTE}'"

rsync -az --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude 'deploy/' \
  "${ROOT}/" "${HOST}:${REMOTE}/"

# root-owned 600 — qaguru cannot read it; systemd/docker compose run as root.
scp -q "${ENV_FILE}" "${HOST}:/tmp/keycloak.env"
scp -q "${SCRIPT_DIR}/keycloak.service" "${HOST}:/tmp/keycloak.service"
scp -q "${SCRIPT_DIR}/keycloak-pg-dump.sh" "${HOST}:/tmp/keycloak-pg-dump.sh"
scp -q "${SCRIPT_DIR}/keycloak-pg-dump.service" "${HOST}:/tmp/keycloak-pg-dump.service"
scp -q "${SCRIPT_DIR}/keycloak-pg-dump.timer" "${HOST}:/tmp/keycloak-pg-dump.timer"
# The dump script resolves s3.py next to itself, so they install as a pair.
scp -q "${SCRIPT_DIR}/s3.py" "${HOST}:/tmp/s3.py"

ssh "${HOST}" 'sudo bash -s' <<'REMOTE'
set -euo pipefail
install -m 600 /tmp/keycloak.env /etc/keycloak/keycloak.env
rm -f /tmp/keycloak.env
chown root:root /etc/keycloak/keycloak.env

set -a
# shellcheck disable=SC1091
source /etc/keycloak/keycloak.env
set +a

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='keycloak'" | grep -q 1; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 \
    -c "CREATE USER keycloak WITH PASSWORD '${POSTGRES_PASSWORD}'"
else
  sudo -u postgres psql -v ON_ERROR_STOP=1 \
    -c "ALTER USER keycloak WITH PASSWORD '${POSTGRES_PASSWORD}'"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='keycloak'" | grep -q 1; then
  sudo -u postgres createdb -O keycloak keycloak
fi

install -m 644 /tmp/keycloak.service /etc/systemd/system/keycloak.service
install -m 755 /tmp/keycloak-pg-dump.sh /usr/local/sbin/keycloak-pg-dump.sh
install -m 755 /tmp/s3.py /usr/local/sbin/s3.py
install -m 644 /tmp/keycloak-pg-dump.service /etc/systemd/system/keycloak-pg-dump.service
install -m 644 /tmp/keycloak-pg-dump.timer /etc/systemd/system/keycloak-pg-dump.timer
rm -f /tmp/keycloak.service /tmp/keycloak-pg-dump.sh /tmp/s3.py /tmp/keycloak-pg-dump.service /tmp/keycloak-pg-dump.timer

systemctl daemon-reload
systemctl enable --now keycloak-pg-dump.timer
systemctl enable keycloak.service
systemctl restart keycloak.service
REMOTE

echo "waiting for Keycloak health on ${HOST} (first --optimized import can take a minute)"
for _ in $(seq 1 60); do
  if ssh "${HOST}" 'curl -sf --max-time 5 http://127.0.0.1:9000/health/ready >/dev/null'; then
    echo "OK: Keycloak ready on ${HOST}:${REMOTE}"
    exit 0
  fi
  sleep 5
done
echo "FAIL: Keycloak health did not become ready. Logs:" >&2
ssh "${HOST}" 'sudo docker compose -f /opt/auth.qa.guru/docker-compose.yml -f /opt/auth.qa.guru/docker-compose.prod.yml logs --tail=80' >&2 || true
exit 1
