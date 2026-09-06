#!/usr/bin/env bash
# Generate ~/.config/auth-qa-guru/keycloak.env (mode 600). Never overwrites.
# Do not print secrets. Prod host copy is installed by deploy/install.sh.
set -euo pipefail

DEST="${AUTH_ENV:-${HOME}/.config/auth-qa-guru/keycloak.env}"
EXAMPLE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env.example"

if [[ -f "${DEST}" ]]; then
  echo "exists: ${DEST} (not overwriting)"
  exit 0
fi

rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-32; }

mkdir -p "$(dirname "${DEST}")"
umask 077
pw_pg="$(rand)"
pw_admin="$(rand)"
cat > "${DEST}" <<EOF
# Generated $(date -u +%Y-%m-%dT%H:%M:%SZ). Root-owned copy on the IdP host:
# /etc/keycloak/keycloak.env. Not Vault (ADR 017 §12).
POSTGRES_DB=keycloak
POSTGRES_USER=keycloak
POSTGRES_PASSWORD=${pw_pg}
KC_DB_PASSWORD=${pw_pg}

KC_BOOTSTRAP_ADMIN_USERNAME=admin
KC_BOOTSTRAP_ADMIN_PASSWORD=${pw_admin}

KC_WEBAUTHN_RP_ID=qa.guru
KC_HOSTNAME=https://auth.qa.guru

KC_GITHUB_CLIENT_ID=github-oauth-app-not-configured
KC_GITHUB_CLIENT_SECRET=github-oauth-app-not-configured

KC_CLIENT_SECRET_GRAFANA=$(rand)
KC_CLIENT_SECRET_JENKINS=$(rand)
KC_CLIENT_SECRET_GITLAB=$(rand)
KC_CLIENT_SECRET_TESTOPS=$(rand)
KC_CLIENT_SECRET_OAUTH2_PROXY=$(rand)
KC_CLIENT_SECRET_PROVISIONING=$(rand)

KC_SMTP_HOST=
KC_SMTP_PORT=465
KC_SMTP_FROM=
KC_SMTP_FROM_DISPLAY="QA Guru"
KC_SMTP_SSL=true
KC_SMTP_STARTTLS=false
KC_SMTP_AUTH=true
KC_SMTP_USER=
KC_SMTP_PASSWORD=
EOF
chmod 600 "${DEST}"
echo "wrote ${DEST} (mode 600). Template was ${EXAMPLE}."
echo "break-glass admin username: admin (password is in the env file, not here)"
