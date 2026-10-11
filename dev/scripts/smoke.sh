#!/usr/bin/env bash
# Stand smoke: Keycloak up, realm discoverable, BOTH protocols served, login page wired.
# Sonar Community Build only speaks SAML and everyone else speaks OIDC — an IdP that serves
# one of the two would not have closed the matrix (ADR 017 п. 1).
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
set -a; . ./.env; set +a
BASE="http://localhost:${KEYCLOAK_HTTP_PORT:-8543}"
REALM="${REALM:-qaguru}"
# RFC 7636 example challenge — account-console requires PKCE; this is a public test vector.
PKCE="code_challenge_method=S256&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
LOGIN="$BASE/realms/$REALM/protocol/openid-connect/auth?client_id=account-console&redirect_uri=$BASE/realms/$REALM/account/&response_type=code&scope=openid&$PKCE"

fail=0
check() {
  local label="$1" url="$2" needle="${3:-}"
  local body code
  body=$(curl -sSL -w $'\n%{http_code}' "$url") || { echo "FAIL  $label — curl error"; fail=1; return; }
  code=$(printf '%s' "$body" | tail -1)
  body=$(printf '%s' "$body" | sed '$d')
  if [ "${code:0:1}" != "2" ]; then echo "FAIL  $label — HTTP $code"; fail=1; return; fi
  if [ -n "$needle" ] && ! printf '%s' "$body" | grep -q -- "$needle"; then
    echo "FAIL  $label — no '$needle' in response"; fail=1; return
  fi
  echo "ok    $label"
}

check "oidc discovery"    "$BASE/realms/$REALM/.well-known/openid-configuration" 'openid-connect/auth'
check "jwks"              "$BASE/realms/$REALM/protocol/openid-connect/certs"    '"keys"'
check "saml descriptor"   "$BASE/realms/$REALM/protocol/saml/descriptor"         'EntityDescriptor'
check "login form"        "$LOGIN" 'id="kc-form-login"'
check "password fallback" "$LOGIN" 'id="password"'
check "forgot password"   "$LOGIN" 'reset-credentials'
check "forgot password ru" "$LOGIN" 'Забыли пароль?'
check "passkey button"    "$LOGIN" 'id="authenticateWebAuthnButton"'
check "github brokering"  "$LOGIN" 'broker/github/login'
check "qaguru login theme" "$LOGIN" '/login/qaguru/'

exit "$fail"
