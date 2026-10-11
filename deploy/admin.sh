#!/usr/bin/env bash
# Keycloak admin console through the IdP VM: nginx serves master sign-in and the
# consoles only to the VM itself, so the browser rides an SSH SOCKS tunnel.
#
#   ./deploy/admin.sh          # quit the browser (Cmd+Q) to close the tunnel
set -euo pipefail

HOST="${AUTH_DEPLOY_HOST:-auth-qa-guru}"
PORT="${AUTH_SOCKS_PORT:-1080}"
URL="${AUTH_ADMIN_URL:-https://auth.qa.guru/admin/master/console/}"
BROWSER="${AUTH_ADMIN_BROWSER:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PROFILE="${HOME}/.config/auth-qa-guru/admin-browser"

ssh -N -o BatchMode=yes -o LogLevel=ERROR -o ExitOnForwardFailure=yes -D "127.0.0.1:${PORT}" "${HOST}" &
tunnel=$!
trap 'kill "${tunnel}" 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  nc -z 127.0.0.1 "${PORT}" 2>/dev/null && break
  kill -0 "${tunnel}" 2>/dev/null || { echo "FAIL: SOCKS tunnel to ${HOST} did not start (port ${PORT} busy? set AUTH_SOCKS_PORT)" >&2; exit 1; }
  sleep 0.25
done

mkdir -p "${PROFILE}"
"${BROWSER}" --user-data-dir="${PROFILE}" --proxy-server="socks5://127.0.0.1:${PORT}" --no-first-run "${URL}"
