#!/usr/bin/env python3
"""Prod login theme qaguru — copy files into the running container, PUT loginTheme.

Does not stop Keycloak. Does not PUT clients. Does not LDAP / P9.

  python3 deploy/theme.py apply
  python3 deploy/theme.py status
  python3 deploy/theme.py login-check
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[assignment]

REALM = "qaguru"
THEME = "qaguru"
AUTH_URL = os.environ.get("AUTH_URL", "https://auth.qa.guru").rstrip("/")
HOST = os.environ.get("AUTH_SSH", "auth-qa-guru")
REMOTE = "/opt/auth.qa.guru"
CONTAINER = os.environ.get("AUTH_CONTAINER", "auth-qa-guru-keycloak-1")
AUTH_ENV_FILE = Path(os.environ.get("AUTH_ENV", Path.home() / ".config/auth-qa-guru/keycloak.env"))
LIVE_STAFF = "svasenkov"
PKCE = "code_challenge_method=S256&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
DROP_FROM_REALM_PUT = ("clients", "users", "groups", "identityProviders", "components", "authenticationFlows")
FORM_NEEDLES = (
    'id="kc-form-login"',
    'id="username"',
    'id="password"',
    'id="kc-login"',
    'id="authenticateWebAuthnButton"',
    "broker/github/login",
)

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT.parent
THEME_SRC = WRAPPER / "dev" / "themes" / THEME


def ssl_ctx() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def load_kv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def auth_env() -> dict[str, str]:
    env = load_kv(AUTH_ENV_FILE)
    missing = [k for k in ("KC_BOOTSTRAP_ADMIN_USERNAME", "KC_BOOTSTRAP_ADMIN_PASSWORD") if not env.get(k)]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)} in {AUTH_ENV_FILE}")
    return env


def kc(method: str, path: str, token: str, body: Any = None) -> Any:
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{AUTH_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_ctx()) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> {exc.code} {exc.read()[:400]!r}") from exc


def keycloak_token(env: dict[str, str]) -> str:
    form = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": env["KC_BOOTSTRAP_ADMIN_USERNAME"],
            "password": env["KC_BOOTSTRAP_ADMIN_PASSWORD"],
        }
    ).encode()
    req = urllib.request.Request(
        f"{AUTH_URL}/realms/master/protocol/openid-connect/token", data=form, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
        return json.loads(resp.read())["access_token"]


def ssh(script: str, *, check: bool = True, timeout: int = 120) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", HOST, "bash", "-s"],
        input=script.encode(),
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise SystemExit(f"ssh {HOST} failed ({proc.returncode}): {(err or out)[:1200]}")
    return out


def sync_theme_into_clone() -> Path:
    if not (THEME_SRC / "login" / "theme.properties").is_file():
        raise SystemExit(f"missing theme SSOT {THEME_SRC}")
    dest = ROOT / "themes" / THEME
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", f"{THEME_SRC}/", f"{dest}/"], check=True)
    return dest


def copy_theme_to_host() -> dict[str, str]:
    dest = sync_theme_into_clone()
    ssh(f"mkdir -p '{REMOTE}/themes/{THEME}'")
    subprocess.run(
        ["rsync", "-az", "--delete", f"{dest}/", f"{HOST}:{REMOTE}/themes/{THEME}/"],
        check=True,
    )
    compose_src = ROOT / "docker-compose.yml"
    subprocess.run(["rsync", "-az", str(compose_src), f"{HOST}:{REMOTE}/docker-compose.yml"], check=True)
    # Live copy into the running container. Do not compose up / systemctl restart.
    remote = ssh(
        f"""
set -euo pipefail
test -f '{REMOTE}/themes/{THEME}/login/theme.properties'
cid="$(sudo docker ps --filter name={CONTAINER} --format '{{{{.ID}}}}' | head -1)"
if [ -z "$cid" ]; then
  cid="$(sudo docker ps --filter ancestor=auth-qa-guru-keycloak:26.7.3 --format '{{{{.ID}}}}' | head -1)"
fi
test -n "$cid"
sudo docker exec "$cid" mkdir -p /opt/keycloak/themes/{THEME}
sudo docker cp '{REMOTE}/themes/{THEME}/.' "$cid:/opt/keycloak/themes/{THEME}/"
sudo docker exec "$cid" test -f /opt/keycloak/themes/{THEME}/login/theme.properties
# Health must still be 200 — we did not restart.
curl -sf --max-time 5 http://127.0.0.1:9000/health/ready >/dev/null
echo "container:$cid"
"""
    )
    return {"host": f"{REMOTE}/themes/{THEME}", "docker": remote.strip()}


def client_ids(token: str) -> list[str]:
    clients = kc("GET", f"/admin/realms/{REALM}/clients", token) or []
    return sorted(c.get("clientId") or "" for c in clients)


def apply_login_theme(env: dict[str, str]) -> dict[str, Any]:
    token = keycloak_token(env)
    before = client_ids(token)
    realm = kc("GET", f"/admin/realms/{REALM}", token)
    if realm.get("loginWithEmailAllowed") is not False:
        raise SystemExit("refusing: loginWithEmailAllowed is on")
    if realm.get("resetPasswordAllowed") is not True:
        raise SystemExit("refusing: resetPasswordAllowed is off")
    for key in DROP_FROM_REALM_PUT:
        realm.pop(key, None)
    realm["loginTheme"] = THEME
    kc("PUT", f"/admin/realms/{REALM}", token, realm)
    after_realm = kc("GET", f"/admin/realms/{REALM}", token)
    after = client_ids(token)
    if after != before:
        raise SystemExit("clients changed after loginTheme PUT — stopping")
    return {
        "loginTheme": after_realm.get("loginTheme"),
        "accountTheme": after_realm.get("accountTheme"),
        "clients": len(after),
    }


def fetch_login_html() -> str:
    account = f"{AUTH_URL}/realms/{REALM}/account/"
    url = (
        f"{AUTH_URL}/realms/{REALM}/protocol/openid-connect/auth"
        f"?client_id=account-console&redirect_uri={urllib.parse.quote(account, safe='')}"
        f"&response_type=code&scope=openid&{PKCE}"
    )
    req = urllib.request.Request(url, headers={"Accept": "text/html", "User-Agent": "qa-guru-theme-check"})
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
        return resp.read().decode("utf-8", "replace")


def verify_html() -> dict[str, Any]:
    html = fetch_login_html()
    missing = [n for n in FORM_NEEDLES if n not in html]
    css_ok = f"/login/{THEME}/" in html and "css/login.css" in html
    ds_header = 'data-testid="header"' in html
    header_css = "css/header.css" in html and "css/shell.css" in html
    return {
        "css": css_ok,
        "ds_header": ds_header,
        "header_css": header_css,
        "form_missing": missing,
        "svasenkov_in_html": LIVE_STAFF in html.lower(),
        "ok": css_ok and ds_header and header_css and not missing and LIVE_STAFF not in html.lower(),
        "len": len(html),
    }


def cmd_apply() -> int:
    copied = copy_theme_to_host()
    realm = apply_login_theme(auth_env())
    html = verify_html()
    result = {"ok": realm.get("loginTheme") == THEME and html["ok"], "copied": copied, "realm": realm, "html": html}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def cmd_status() -> int:
    env = auth_env()
    token = keycloak_token(env)
    realm = kc("GET", f"/admin/realms/{REALM}", token)
    html = verify_html()
    health = ssh("curl -sf --max-time 5 http://127.0.0.1:9000/health/ready; echo; sudo docker inspect -f '{{.State.Running}} {{.Name}}' $(sudo docker ps -q --filter name=keycloak) | head -5")
    result = {
        "ok": realm.get("loginTheme") == THEME and html["ok"],
        "loginTheme": realm.get("loginTheme"),
        "html": html,
        "health": health.strip().splitlines()[:6],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def cmd_login_check() -> int:
    script = WRAPPER / "dev" / "scripts" / "theme-login-check.mjs"
    proc = subprocess.run(["node", str(script), "--prod"], check=False)
    return proc.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=("apply", "status", "login-check"))
    args = parser.parse_args()
    cmds = {"apply": cmd_apply, "status": cmd_status, "login-check": cmd_login_check}
    raise SystemExit(cmds[args.cmd]())


if __name__ == "__main__":
    main()
