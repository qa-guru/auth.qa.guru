#!/usr/bin/env bash
# Restore the Keycloak DB from a fresh dump WHILE the realm is still empty.
# Criterion P2a: prove recovery before any live person is in the realm.
#
# 1. dump on the host
# 2. copy the dump off-box (operator ~/.config/auth-qa-guru/backups/)
# 3. drop+restore the live database, restart Keycloak, wait health
#
# THIS SCRIPT DROPS THE LIVE DATABASE. That was safe in P2a, when the realm was
# empty by definition. It is not safe now: the realm carries live people, and
# `dropdb` would take everything created since the dump it restores. Once people
# exist, restorability is proven non-destructively against a scratch database:
#
#   deploy/offbox.py restore-check
#
# The guard below refuses rather than trusting whoever runs this to remember.
set -euo pipefail

HOST="${AUTH_DEPLOY_HOST:-auth-qa-guru}"
OFFBOX="${AUTH_BACKUP_DIR:-${HOME}/.config/auth-qa-guru/backups}"

humans="$(ssh "${HOST}" "sudo -u postgres psql -d keycloak -Atc \"select count(*) from user_entity u join realm r on r.id = u.realm_id where r.name = 'qaguru' and u.service_account_client_link is null;\"")"
if [[ "${humans}" != "0" && "${ALLOW_DESTRUCTIVE_RESTORE:-}" != "yes" ]]; then
  cat >&2 <<EOF
REFUSING: realm qaguru holds ${humans} live people and this script drops the database.

Prove restorability without touching prod:
  python3 deploy/offbox.py restore-check

If a real disaster restore is genuinely intended, take a dump of the CURRENT
state first (ADR 017 §гейт сохранности п. 5), then:
  ALLOW_DESTRUCTIVE_RESTORE=yes $0
EOF
  exit 2
fi

mkdir -p "${OFFBOX}"
chmod 700 "${OFFBOX}"

echo "dumping"
ssh "${HOST}" 'sudo /usr/local/sbin/keycloak-pg-dump.sh'
# glob must expand as root: /var/backups/keycloak is 750 postgres:postgres
remote_dump="$(ssh "${HOST}" "sudo bash -c 'ls -1t /var/backups/keycloak/keycloak-*.dump 2>/dev/null | head -1'")"
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
# compose down to drop the JDBC session (both files — same project as systemd)
docker compose --project-directory /opt/auth.qa.guru \
  --env-file /etc/keycloak/keycloak.env \
  -f /opt/auth.qa.guru/docker-compose.yml \
  -f /opt/auth.qa.guru/docker-compose.prod.yml down || true
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
