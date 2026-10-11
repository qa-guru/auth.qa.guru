"""Shared Keycloak admin helpers for the auth stand scripts.

Reads an env file (stand `dev/.env` or prod `~/.config/auth-qa-guru/keycloak.env`) and talks to the Admin REST API.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEV_DIR = Path(__file__).resolve().parents[1]
REALM = os.environ.get("REALM", "qaguru")
PROD_ENV = Path.home() / ".config/auth-qa-guru/keycloak.env"


def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except Exception:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


CTX = ssl_ctx()


def load_env(env_file: Path | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    path = Path(env_file) if env_file is not None else DEV_DIR / ".env"
    if not path.is_file():
        hint = "run scripts/init-env.sh first" if env_file is None else "mode 600, not in git"
        raise SystemExit(f"missing {path} — {hint}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def base_url(env: dict[str, str]) -> str:
    explicit = (env.get("KEYCLOAK_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    hostname = (env.get("KC_HOSTNAME") or "").strip().rstrip("/")
    if hostname.startswith("http://") or hostname.startswith("https://"):
        return hostname
    if hostname and hostname not in ("localhost", "127.0.0.1"):
        return f"https://{hostname}"
    return f"http://localhost:{env.get('KEYCLOAK_HTTP_PORT', '8543')}"


def _request(url: str, *, method: str, token: str | None, body: Any = None) -> Any:
    data = None
    headers = {}
    if isinstance(body, (dict, list)) and method != "POST_FORM":
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {url} -> {exc.code} {exc.read().decode()[:300]}") from exc
    if not raw:
        return None
    return json.loads(raw)


def admin_token(env: dict[str, str]) -> str:
    form = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": env["KC_BOOTSTRAP_ADMIN_USERNAME"],
            "password": env["KC_BOOTSTRAP_ADMIN_PASSWORD"],
        }
    ).encode()
    url = f"{base_url(env)}/realms/master/protocol/openid-connect/token"
    req = urllib.request.Request(url, data=form, method="POST")
    with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def get(env: dict[str, str], token: str, path: str) -> Any:
    return _request(f"{base_url(env)}{path}", method="GET", token=token)


def post(env: dict[str, str], token: str, path: str, body: Any) -> Any:
    return _request(f"{base_url(env)}{path}", method="POST", token=token, body=body)


def put(env: dict[str, str], token: str, path: str, body: Any) -> Any:
    return _request(f"{base_url(env)}{path}", method="PUT", token=token, body=body)


def delete(env: dict[str, str], token: str, path: str) -> Any:
    return _request(f"{base_url(env)}{path}", method="DELETE", token=token)


DROP_FROM_REALM_PUT = ("clients", "users", "groups", "identityProviders", "components", "authenticationFlows")


def client_ids(env: dict[str, str], token: str, realm: str) -> list[str]:
    clients = get(env, token, f"/admin/realms/{realm}/clients") or []
    return sorted(c.get("clientId") or "" for c in clients)


def put_realm_fields(
    env: dict[str, str],
    token: str,
    realm: str,
    fields: dict[str, Any],
    *,
    school_guards: bool = False,
) -> dict[str, Any]:
    """PUT selected realm fields. Never PUT clients/users/groups. master has no school guards."""
    before = client_ids(env, token, realm)
    body = get(env, token, f"/admin/realms/{realm}")
    if school_guards:
        if body.get("loginWithEmailAllowed") is not False:
            raise SystemExit(f"refusing {realm}: loginWithEmailAllowed is on")
        if body.get("resetPasswordAllowed") is not True:
            raise SystemExit(f"refusing {realm}: resetPasswordAllowed is off")
    for key in DROP_FROM_REALM_PUT:
        body.pop(key, None)
    body.update(fields)
    put(env, token, f"/admin/realms/{realm}", body)
    after = get(env, token, f"/admin/realms/{realm}")
    after_clients = client_ids(env, token, realm)
    if after_clients != before:
        raise SystemExit(f"clients changed on {realm} — abort")
    if school_guards and after.get("loginWithEmailAllowed") is not False:
        raise SystemExit(f"realm fields applied on {realm} but loginWithEmailAllowed flipped on")
    snapshot = {key: after.get(key) for key in fields}
    snapshot["realm"] = realm
    snapshot["clients"] = len(after_clients)
    return snapshot


def put_login_theme(
    env: dict[str, str],
    token: str,
    realm: str,
    theme: str = "qaguru",
    *,
    school_guards: bool = False,
) -> dict[str, Any]:
    """Set loginTheme only. Never PUT clients. master has no school email/reset guards."""
    out = put_realm_fields(env, token, realm, {"loginTheme": theme}, school_guards=school_guards)
    after = get(env, token, f"/admin/realms/{realm}")
    return {
        "realm": realm,
        "loginTheme": after.get("loginTheme"),
        "accountTheme": after.get("accountTheme"),
        "clients": out["clients"],
    }
