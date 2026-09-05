#!/bin/sh
# Managed by auth.qa.guru/deploy/configure-tls.sh
# certonly --webroot leaves no installer, so certbot renews files while nginx
# keeps the old cert in memory. Without this hook the stand goes stale ~60 days in.
set -e
nginx -t && systemctl reload nginx
