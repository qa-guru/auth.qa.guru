#!/usr/bin/env bash
# Daily Keycloak pg_dump, local + off-box. Runs as root via systemd.
#
# The off-box leg is NOT optional. Until 2026-09-05 this script skipped S3
# whenever /etc/keycloak/s3.env was missing and still exited 0, so the unit
# looked healthy while the only copies of a realm with 99 live people sat on
# the same disk as the database. A missing or broken receiver now fails the
# unit, which is the whole point of having a timer.
#
# Deliberately local-only (no off-box receiver yet)? Then say so on purpose:
#   touch /etc/keycloak/backup-local-only
set -euo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DIR=/var/backups/keycloak
DUMP="${DIR}/keycloak-${STAMP}.dump"
S3_ENV=/etc/keycloak/s3.env
LOCAL_ONLY=/etc/keycloak/backup-local-only
S3_CLIENT="$(dirname "$(readlink -f "$0")")/s3.py"
RETAIN_LOCAL_DAYS="${RETAIN_LOCAL_DAYS:-14}"

install -d -m 750 -o postgres -g postgres "${DIR}"
sudo -u postgres pg_dump -Fc -d keycloak -f "${DUMP}"
chmod 640 "${DUMP}"
chown postgres:postgres "${DUMP}"

# A dump of an empty realm is ~250 KB and restores clean, so size alone is a
# weak signal — assert the realm actually still has people in it.
humans="$(sudo -u postgres psql -d keycloak -Atc "select count(*) from user_entity u join realm r on r.id = u.realm_id where r.name = 'qaguru' and u.service_account_client_link is null;")"
echo "dumped ${DUMP} (realm qaguru: ${humans} humans)"

find "${DIR}" -name 'keycloak-*.dump' -mtime "+${RETAIN_LOCAL_DAYS}" -delete

if [ -f "${LOCAL_ONLY}" ]; then
  echo "WARNING: ${LOCAL_ONLY} present — off-box upload skipped on purpose."
  echo "WARNING: the only copies of ${humans} accounts are on this host's disk."
  exit 0
fi

if [ ! -f "${S3_ENV}" ]; then
  echo "FAIL: ${S3_ENV} missing — no off-box copy of ${humans} accounts." >&2
  echo "Fix: deploy/offbox.py provision   (or touch ${LOCAL_ONLY} to accept the risk)" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "${S3_ENV}"
set +a

# s3.py re-reads the object with HEAD and compares length, so "uploaded" here
# means the bytes are actually retrievable off-box, not merely that PUT got 200.
python3 "${S3_CLIENT}" put "${DUMP}"
