#!/usr/bin/env python
"""P9 — learn.qa.guru as Keycloak OIDC client (smoke RS, not the LMS product).

    python scripts/learn.py seed-env
    python scripts/learn.py inventory
    python scripts/learn.py apply-client          # stand :8543
    python scripts/learn.py apply-client --prod   # https://auth.qa.guru
    python scripts/learn.py login-check
    python scripts/learn.py console-check
    python scripts/learn.py verify
    python scripts/learn.py break-glass
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

CLIENT_ID = "learn"
STUB_PORT = 3040
INV_DIR = Path.home() / ".config" / "auth-qa-guru" / "p9-inventory"
PROD_LEARN_ENV = Path.home() / ".config" / "auth-qa-guru" / "learn.env"
GROUPS_MAPPER = {
    "name": "groups",
    "protocol": "openid-connect",
    "protocolMapper": "oidc-group-membership-mapper",
    "config": {
        "full.path": "true",
        "claim.name": "groups",
        "id.token.claim": "true",
        "access.token.claim": "true",
        "introspection.token.claim": "true",
        "userinfo.token.claim": "true",
    },
}
ENV_KEYS = (
    "KC_CLIENT_SECRET_LEARN",
    "LEARN_SESSION_SECRET",
    "LEARN_BREAKGLASS_PASSWORD",
)


class Session:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", "qa-guru-learn-p9/1.0")]

    def fetch(self, url: str, data: bytes | None = None) -> tuple[str, str, int]:
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        try:
            with self.opener.open(req, timeout=45) as resp:
                return resp.geturl(), resp.read().decode("utf-8", "replace"), resp.status
        except urllib.error.HTTPError as exc:
            return url, exc.read().decode("utf-8", "replace"), exc.code
        except urllib.error.URLError as exc:
            return url, f"transport error: {exc.reason}", 0


def stub_url(env: dict[str, str]) -> str:
    port = env.get("LEARN_STUB_PORT") or str(STUB_PORT)
    return f"http://127.0.0.1:{port}"


def rand_secret() -> str:
    return secrets.token_urlsafe(24)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="P9 learn.qa.guru OIDC client")
    p.add_argument(
        "command",
        choices=("seed-env", "inventory", "apply-client", "login-check", "console-check", "verify", "break-glass"),
    )
    p.add_argument("--prod", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def realm_learn_client() -> dict[str, Any]:
    realm = json.loads((kc.DEV_DIR / "realm" / "qaguru-realm.json").read_text(encoding="utf-8"))
    for client in realm.get("clients") or []:
        if client.get("clientId") == CLIENT_ID:
            return client
    raise SystemExit("learn client missing from realm/qaguru-realm.json")


def subst_secrets(node: Any, env: dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: subst_secrets(v, env) for k, v in node.items()}
    if isinstance(node, list):
        return [subst_secrets(v, env) for v in node]
    if isinstance(node, str) and node.startswith("${") and node.endswith("}"):
        key = node[2:-1]
        value = env.get(key)
        if not value:
            raise SystemExit(f"placeholder {node} empty — python scripts/learn.py seed-env")
        return value
    return node


def client_by_id(env: dict[str, str], token: str, client_id: str) -> dict[str, Any] | None:
    found = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients?clientId={client_id}")
    return found[0] if found else None


def cmd_seed_env() -> int:
    path = kc.DEV_DIR / ".env"
    if not path.is_file():
        raise SystemExit("dev/.env missing — run scripts/init-env.sh")
    existing = kc.load_env()
    added: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    text = path.read_text(encoding="utf-8")
    if "LEARN_STUB_PORT=" not in text:
        lines.append("LEARN_STUB_PORT=3040")
        added.append("LEARN_STUB_PORT")
    if "LEARN_BREAKGLASS_USERNAME=" not in text:
        lines.append("LEARN_BREAKGLASS_USERNAME=admin")
        added.append("LEARN_BREAKGLASS_USERNAME")
    for key in ENV_KEYS:
        if existing.get(key):
            continue
        lines.append(f"{key}={rand_secret()}")
        added.append(key)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print(json.dumps({"ok": True, "path": str(path), "added": added}, ensure_ascii=False))
    return 0


def people_snapshot(env: dict[str, str], token: str) -> dict[str, Any]:
    count = kc.get(env, token, f"/admin/realms/{kc.REALM}/users/count")
    users = kc.get(env, token, f"/admin/realms/{kc.REALM}/users?briefRepresentation=true&max=500") or []
    names = sorted(u.get("username") or "" for u in users)
    groups = kc.get(env, token, f"/admin/realms/{kc.REALM}/groups") or []
    return {
        "count": count,
        "usernames": names,
        "group_paths": sorted(g.get("path") or "" for g in groups),
    }


def cmd_inventory(prod: bool) -> int:
    env = kc.load_env(kc.PROD_ENV if prod else None)
    token = kc.admin_token(env)
    snap = {
        "when": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prod": prod,
        "base_url": kc.base_url(env),
        "people": people_snapshot(env, token),
        "learn_client": bool(client_by_id(env, token, CLIENT_ID)),
    }
    INV_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(INV_DIR, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = INV_DIR / "before.json"
    if target.exists():
        target = INV_DIR / f"inventory-{stamp}.json"
    target.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    print(
        json.dumps(
            {
                "ok": True,
                "file": str(target),
                "count": snap["people"]["count"],
                "groups": snap["people"]["group_paths"],
                "learn_client": snap["learn_client"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def ensure_prod_learn_secret() -> str:
    env = {}
    if PROD_LEARN_ENV.is_file():
        env = kc.load_env(PROD_LEARN_ENV)
    secret = (env.get("KC_CLIENT_SECRET_LEARN") or "").strip()
    if secret:
        return secret
    secret = rand_secret()
    PROD_LEARN_ENV.parent.mkdir(parents=True, exist_ok=True)
    previous = PROD_LEARN_ENV.read_text(encoding="utf-8") if PROD_LEARN_ENV.is_file() else ""
    line = f"KC_CLIENT_SECRET_LEARN={secret}\n"
    PROD_LEARN_ENV.write_text(previous + ("" if previous.endswith("\n") or not previous else "\n") + line, encoding="utf-8")
    os.chmod(PROD_LEARN_ENV, 0o600)
    return secret


def cmd_apply_client(*, prod: bool, dry_run: bool) -> int:
    wanted = realm_learn_client()
    plan = {
        "ok": True,
        "dry_run": dry_run,
        "prod": prod,
        "client": CLIENT_ID,
        "redirectUris": wanted.get("redirectUris"),
    }
    if dry_run:
        print(json.dumps(plan, ensure_ascii=False))
        return 0
    if prod:
        env = kc.load_env(kc.PROD_ENV)
        env = dict(env)
        env["KC_CLIENT_SECRET_LEARN"] = ensure_prod_learn_secret()
    else:
        env = kc.load_env()
    payload = subst_secrets(wanted, env)
    token = kc.admin_token(env)
    live = client_by_id(env, token, CLIENT_ID)
    if live is None:
        kc.post(env, token, f"/admin/realms/{kc.REALM}/clients", payload)
        action = "created"
        live = client_by_id(env, token, CLIENT_ID)
        if live is None:
            raise SystemExit("learn client missing after create")
    else:
        uuid = live["id"]
        current = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}")
        current["redirectUris"] = sorted(set(current.get("redirectUris") or []) | set(payload["redirectUris"]))
        current["webOrigins"] = sorted(set(current.get("webOrigins") or []) | set(payload["webOrigins"]))
        attrs = current.setdefault("attributes", {})
        attrs["pkce.code.challenge.method"] = "S256"
        attrs["post.logout.redirect.uris"] = payload["attributes"]["post.logout.redirect.uris"]
        current["name"] = payload["name"]
        current["description"] = payload["description"]
        current["publicClient"] = False
        current["standardFlowEnabled"] = True
        current["directAccessGrantsEnabled"] = False
        kc.put(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}", current)
        mappers = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}/protocol-mappers/models") or []
        if not any(m.get("name") == "groups" for m in mappers):
            kc.post(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}/protocol-mappers/models", GROUPS_MAPPER)
        action = "updated"
        live = current
    print(
        json.dumps(
            {
                **plan,
                "action": action,
                "base_url": kc.base_url(env),
                "redirectUris": live.get("redirectUris"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def parse_who(html: str) -> dict[str, str]:
    match = re.search(
        r'id="who"[^>]*data-username="([^"]*)"[^>]*data-role="([^"]*)"[^>]*data-auth="([^"]*)"',
        html,
    )
    if not match:
        return {}
    return {"username": match.group(1), "role": match.group(2), "auth": match.group(3)}


def cmd_login_check() -> int:
    script = Path(__file__).resolve().parent / "learn-login-check.mjs"
    proc = subprocess.run(["node", str(script)], cwd=str(kc.DEV_DIR), check=False)
    return proc.returncode


def cmd_console_check() -> int:
    return cmd_login_check()


def cmd_verify() -> int:
    env = kc.load_env()
    token = kc.admin_token(env)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    client = client_by_id(env, token, CLIENT_ID)
    check("learn client exists", client is not None)
    check("learn speaks OIDC", (client or {}).get("protocol") == "openid-connect")
    check("learn is confidential", (client or {}).get("publicClient") is False)
    uris = (client or {}).get("redirectUris") or []
    check("stand callback registered", "http://127.0.0.1:3040/oauth2/callback" in uris, ", ".join(uris))
    check("prod callback registered", "https://learn.qa.guru/oauth2/callback" in uris)
    pkce = ((client or {}).get("attributes") or {}).get("pkce.code.challenge.method")
    check("PKCE S256", pkce == "S256", str(pkce))
    before = INV_DIR / "before.json"
    if before.is_file():
        snap = json.loads(before.read_text(encoding="utf-8"))
        now = people_snapshot(env, token)
        check(
            "people floor",
            now["count"] >= snap["people"]["count"],
            f"{snap['people']['count']} → {now['count']}",
        )
    else:
        check("people floor", False, "no before.json — run inventory first")
    try:
        with urllib.request.urlopen(f"{stub_url(env)}/health", timeout=5) as resp:
            health = json.loads(resp.read())
        check("stub health", health.get("ok") is True, str(health))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        check("stub health", False, str(exc))
    width = max(len(n) for n, _, _ in checks)
    failed = []
    for name, ok, detail in checks:
        print(f"{'ok  ' if ok else 'FAIL'}  {name.ljust(width)}  {detail}".rstrip())
        if not ok:
            failed.append(name)
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


def wait_keycloak(env: dict[str, str], timeout_s: float = 90.0) -> bool:
    url = f"{kc.base_url(env)}/realms/{kc.REALM}/.well-known/openid-configuration"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if 200 <= resp.status < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError, ConnectionError):
            time.sleep(1)
    return False


def cmd_break_glass() -> int:
    env = kc.load_env()
    user = env.get("LEARN_BREAKGLASS_USERNAME") or "admin"
    password = env.get("LEARN_BREAKGLASS_PASSWORD") or ""
    if not password:
        raise SystemExit("LEARN_BREAKGLASS_PASSWORD missing")
    compose = ["docker", "compose", "stop", "keycloak"]
    start = ["docker", "compose", "start", "keycloak"]
    rc = 0
    try:
        stop = subprocess.run(compose, cwd=str(kc.DEV_DIR), check=False, capture_output=True, text=True)
        if stop.returncode != 0:
            print(f"FAIL docker compose stop keycloak: {stop.stderr[:300]}")
            return 1
        down_deadline = time.time() + 20
        while time.time() < down_deadline and wait_keycloak(env, timeout_s=1.5):
            time.sleep(0.5)
        sess = Session()
        form = urllib.parse.urlencode({"username": user, "password": password}).encode()
        url, page, status = sess.fetch(f"{stub_url(env)}/login/system", form)
        who = parse_who(page)
        local_ok = who.get("auth") == "local" and who.get("role") == "staff" and who.get("username") == user
        print(f"{'OK  ' if local_ok else 'FAIL'} local /login/system → {who} http={status} landed={url[:80]}")
        if not local_ok:
            rc = 1
        oidc = Session()
        o_url, o_page, o_status = oidc.fetch(f"{stub_url(env)}/login")
        oidc_down = o_status in {0, 401, 502, 503} or "transport error" in o_page or "Connection refused" in o_page or o_status == 0
        # Keycloak down: login redirect should fail to reach the form.
        oidc_form = "kc-form-login" in o_page
        print(f"{'OK  ' if not oidc_form else 'FAIL'} OIDC while IdP down form={oidc_form} http={o_status} url={o_url[:80]}")
        if oidc_form:
            rc = 1
        _ = oidc_down
    finally:
        subprocess.run(start, cwd=str(kc.DEV_DIR), check=False, capture_output=True, text=True)
        if not wait_keycloak(env):
            print("FAIL Keycloak did not come back")
            rc = 1
        else:
            print("ok   Keycloak back")
    return rc


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    if ns.command == "seed-env":
        return cmd_seed_env()
    if ns.command == "inventory":
        return cmd_inventory(ns.prod)
    if ns.command == "apply-client":
        return cmd_apply_client(prod=ns.prod, dry_run=ns.dry_run)
    if ns.command == "login-check":
        return cmd_login_check()
    if ns.command == "console-check":
        return cmd_console_check()
    if ns.command == "verify":
        return cmd_verify()
    if ns.command == "break-glass":
        return cmd_break_glass()
    raise SystemExit(f"unknown command {ns.command}")


if __name__ == "__main__":
    raise SystemExit(main())
