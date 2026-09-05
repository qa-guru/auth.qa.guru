#!/usr/bin/env bash
# Public smoke: OIDC discovery + SAML descriptor. No secrets printed.
set -euo pipefail

URL="${AUTH_URL:-https://auth.qa.guru}"
HOST="${AUTH_DEPLOY_HOST:-auth-qa-guru}"

oidc="$(curl -sfS --max-time 20 "${URL}/realms/qaguru/.well-known/openid-configuration")"
echo "${oidc}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
iss = d.get("issuer", "")
assert iss == "https://auth.qa.guru/realms/qaguru", iss
assert "authorization_endpoint" in d
print("oidc issuer:", iss)
'

saml="$(curl -sfS --max-time 20 "${URL}/realms/qaguru/protocol/saml/descriptor")"
echo "${saml}" | python3 -c '
import sys
xml = sys.stdin.read()
assert "EntityDescriptor" in xml, xml[:200]
assert "auth.qa.guru" in xml
print("saml descriptor: ok", len(xml), "bytes")
'

health="$(ssh "${HOST}" 'curl -sfS --max-time 10 http://127.0.0.1:9000/health/ready')"
echo "health: ${health}"

echo "OK: ${URL}"
