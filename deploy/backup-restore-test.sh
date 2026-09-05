#!/usr/bin/env bash
# Restore the Keycloak DB from a fresh dump WHILE the realm is still empty.
# Criterion P2a: prove recovery before any live person is in the realm.
#
# 1. dump on the host
# 2. copy the dump off-box (operator ~/.config/auth-qa-guru/backups/)
# 3. drop+restore the live database, restart Keycloak, wait health
set -euo pipefail

HOST="${AUTH_DEPLOY_HOST:-auth-qa-guru}"
OFFBOX="${AUTH_BACKUP_DIR:-${HOME}/.config/auth-qa-guru/backups}"

mkdir -p "${OFFBOX}"
chmod 700 "${OFFBOX}"

echo "dumping"
ssh "${HOST}" 'sudo /usr/local/sbin/keycloak-pg-dump.sh'
remote_dump="$(ssh "${HOST}" 'sudo ls -1t /var/backups/keycloak/keycloak-*.dump | head -1')"
if [[ -z "${remote_dump}" ]]; then
  echo "FAIL: no dump on host" >&2
  exit 1
fi
local_dump="${OFFBOX}/$(basename "${remote_dump}")"
ssh "${HOST}" "sudo cat '${remote_dump}'" > "${local_dump}"
chmod 600 "${local_dump}"
echo "off-box copy: ${local_dump} ($(wc -c < "${local_dump}") bytes)"

echo "restoring ${remote_dump} onto live keycloak db"
ssh "${HOST}" "sudo bash -s -- '${remote_dump}'" <<'REMOTE'
set -euo pipefail
DUMP="$1"
systemctl stop keycloak
# compose down to drop the JDBC session
docker compose --project-directory /opt/auth.qa.guru -f /opt/auth.qa.guru/docker-compose.yml down || true
sudo -u postgres dropdb --if-exists keycloak
sudo -u postgres createdb -O keycloak keycloak
sudo -u postgres pg_restore --exit-on-error -d keycloak "${DUMP}"
systemctl start keycloak
REMOTE

echo "waiting for health after restore"
for _ in $(seq 1 60); do
  if ssh "${HOST}" 'curl -sf --max-time 5 http://127.0.0.1:9000/health/ready >/dev/null'; then
    echo "OK: restored from ${local_dump}"
    exit 0
  fi
  sleep 5
done
echo "FAIL: Keycloak did not become ready after restore" >&2
ssh "${HOST}" 'sudo journalctl -u keycloak -n 40 --no-pager' >&2 || true
exit 1
