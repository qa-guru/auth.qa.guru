#!/usr/bin/env bash
# nginx + Let's Encrypt for auth.qa.guru. Run from the laptop.
#
#   ./deploy/configure-tls.sh
#
# Two passes: HTTP-only vhost so certbot --webroot can answer ACME, then the
# TLS vhost from the repo. Idempotent. Always installs the renew deploy-hook:
# without it certonly --webroot updates files and nginx keeps the old cert.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
HOST="${AUTH_DEPLOY_HOST:-auth-qa-guru}"
FQDN="${AUTH_HOST:-auth.qa.guru}"
EMAIL="${CERTBOT_EMAIL:-admin@qa.guru}"
VHOST_SRC="${ROOT}/nginx/${FQDN}.nginx"

if [[ ! -f "${VHOST_SRC}" ]]; then
  echo "FAIL: ${VHOST_SRC} missing" >&2
  exit 1
fi

scp -q "${SCRIPT_DIR}/reload-nginx.sh" "${HOST}:/tmp/reload-nginx.sh"
scp -q "${VHOST_SRC}" "${HOST}:/tmp/${FQDN}.nginx"

ssh "${HOST}" "sudo FQDN='${FQDN}' EMAIL='${EMAIL}' bash -s" <<'REMOTE'
set -euo pipefail
log() { printf '\n=== %s\n' "$*"; }

log "acme http vhost for ${FQDN}"
install -d -m 755 /var/www/html
cat > "/etc/nginx/sites-available/${FQDN}" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${FQDN};
    location ^~ /.well-known/acme-challenge/ {
        default_type text/plain;
        root /var/www/html;
        try_files \$uri =404;
    }
    location / {
        return 301 https://\$host\$request_uri;
    }
}
NGINX
ln -sf "/etc/nginx/sites-available/${FQDN}" "/etc/nginx/sites-enabled/${FQDN}"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "certificate"
if [ -f "/etc/letsencrypt/live/${FQDN}/fullchain.pem" ]; then
  echo "cert already present — keeping"
else
  certbot certonly --webroot -w /var/www/html -d "${FQDN}" \
    --non-interactive --agree-tos -m "${EMAIL}" --keep-until-expiring
fi

log "certbot tls snippets"
CERTBOT_TLS_CONF=/usr/lib/python3/dist-packages/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf
CERTBOT_DHPARAMS=/usr/lib/python3/dist-packages/certbot/ssl-dhparams.pem
[ -f /etc/letsencrypt/options-ssl-nginx.conf ] \
  || install -m 644 "${CERTBOT_TLS_CONF}" /etc/letsencrypt/options-ssl-nginx.conf
[ -f /etc/letsencrypt/ssl-dhparams.pem ] \
  || install -m 644 "${CERTBOT_DHPARAMS}" /etc/letsencrypt/ssl-dhparams.pem

log "renew deploy hook"
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
install -m 755 /tmp/reload-nginx.sh /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
rm -f /tmp/reload-nginx.sh

log "tls vhost from repo"
install -m 644 "/tmp/${FQDN}.nginx" "/etc/nginx/sites-available/${FQDN}"
rm -f "/tmp/${FQDN}.nginx"
nginx -t
systemctl reload nginx
systemctl is-active nginx
certbot certificates 2>/dev/null | grep -E "Certificate Name|Domains|Expiry" || true
REMOTE

echo "OK: TLS ${FQDN} on ${HOST}"
