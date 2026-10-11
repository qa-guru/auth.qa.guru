#!/usr/bin/env bash
# Generate dev/.env with random stand secrets. Never overwrites an existing .env.
# GitHub OAuth App credentials stay empty — fill them by hand from your own test app.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env already exists — not overwriting. Delete it first if you want fresh secrets."
  exit 0
fi

rand() { openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | cut -c1-24; }

sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand)|" \
  -e "s|^KC_BOOTSTRAP_ADMIN_PASSWORD=.*|KC_BOOTSTRAP_ADMIN_PASSWORD=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_GRAFANA=.*|KC_CLIENT_SECRET_GRAFANA=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_JENKINS=.*|KC_CLIENT_SECRET_JENKINS=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_GITLAB=.*|KC_CLIENT_SECRET_GITLAB=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_TESTOPS=.*|KC_CLIENT_SECRET_TESTOPS=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_OAUTH2_PROXY=.*|KC_CLIENT_SECRET_OAUTH2_PROXY=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_LEARN=.*|KC_CLIENT_SECRET_LEARN=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_AUTOTESTS_AI=.*|KC_CLIENT_SECRET_AUTOTESTS_AI=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_PROVISIONING=.*|KC_CLIENT_SECRET_PROVISIONING=$(rand)|" \
  -e "s|^KC_CLIENT_SECRET_SELENOID_TOKEN=.*|KC_CLIENT_SECRET_SELENOID_TOKEN=$(rand)|" \
  -e "s|^STAND_USER_PASSWORD=.*|STAND_USER_PASSWORD=$(rand)|" \
  -e "s|^LEARN_BREAKGLASS_PASSWORD=.*|LEARN_BREAKGLASS_PASSWORD=$(rand)|" \
  -e "s|^LEARN_SESSION_SECRET=.*|LEARN_SESSION_SECRET=$(rand)|" \
  .env.example > .env

chmod 600 .env
echo "wrote .env (mode 600). Admin password:"
grep '^KC_BOOTSTRAP_ADMIN_PASSWORD=' .env
