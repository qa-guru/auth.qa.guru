#!/usr/bin/env bash
# Bootstrap the IdP VM: ufw, native PostgreSQL, Docker, nginx, certbot.
#
# From the laptop:
#   ./deploy/bootstrap-host.sh
# On the host (after scp):
#   sudo bash /tmp/bootstrap-host.sh --on-host
#
# Idempotent. Does not start Keycloak and does not write secrets.
set -euo pipefail

HOST="${AUTH_DEPLOY_HOST:-auth-qa-guru}"
DOCKER_ENGINE_PKG_VERSION="5:29.6.1-1~ubuntu.24.04~noble"
DOCKER_COMPOSE_PKG_VERSION="5.3.1-1~ubuntu.24.04~noble"

if [[ "${1:-}" != "--on-host" ]]; then
  SCRIPT="${BASH_SOURCE[0]}"
  scp -q "${SCRIPT}" "${HOST}:/tmp/bootstrap-auth-qa-guru.sh"
  ssh "${HOST}" "sudo bash /tmp/bootstrap-auth-qa-guru.sh --on-host"
  echo "OK: bootstrap on ${HOST}"
  exit 0
fi

log() { printf '\n=== %s\n' "$*"; }

log "apt base"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  ca-certificates curl gnupg git jq ufw nginx certbot python3-certbot-nginx \
  postgresql postgresql-contrib python3

log "docker engine ${DOCKER_ENGINE_PKG_VERSION} + compose ${DOCKER_COMPOSE_PKG_VERSION}"
if [ ! -f /etc/apt/sources.list.d/docker.list ]; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
fi
apt-mark unhold docker-ce docker-ce-cli docker-compose-plugin >/dev/null 2>&1 || true
apt-get install -y -qq --allow-downgrades \
  "docker-ce=${DOCKER_ENGINE_PKG_VERSION}" \
  "docker-ce-cli=${DOCKER_ENGINE_PKG_VERSION}" \
  "docker-compose-plugin=${DOCKER_COMPOSE_PKG_VERSION}" \
  containerd.io docker-buildx-plugin
apt-mark hold docker-ce docker-ce-cli docker-compose-plugin
usermod -aG docker qaguru
systemctl enable --now docker
docker version --format 'Engine {{.Server.Version}}'
docker compose version --short

log "ufw — 22/80/443 public; cloud SG default in ru-1 is wide open"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose

log "postgres listen localhost only"
PG_CONF="$(ls /etc/postgresql/*/main/postgresql.conf | head -1)"
PG_HBA="$(ls /etc/postgresql/*/main/pg_hba.conf | head -1)"
sed -i "s/^#\\?listen_addresses.*/listen_addresses = '127.0.0.1'/" "${PG_CONF}"
if ! grep -q 'keycloak' "${PG_HBA}"; then
  printf '\nhost    keycloak    keycloak    127.0.0.1/32    scram-sha-256\n' >> "${PG_HBA}"
fi
systemctl enable --now postgresql
systemctl restart postgresql

install -d -m 755 /opt/auth.qa.guru
chown qaguru:qaguru /opt/auth.qa.guru
install -d -m 700 /etc/keycloak
install -d -m 750 /var/backups/keycloak
chown postgres:postgres /var/backups/keycloak

log "done"
