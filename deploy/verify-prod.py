#!/usr/bin/env python3
"""P2a/P2b acceptance: running prod realm matches ADR 017.

P2b added two pilot people (svasenkov, student-pilot). Stand demos stay off prod.
GitHub brokering stays a P5 item.

  AUTH_ENV=~/.config/auth-qa-guru/keycloak.env python3 deploy/verify-prod.py
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[assignment]

REALM = "qaguru"
BASE = os.environ.get("AUTH_URL", "https://auth.qa.guru")
WRAPPER_REALM = Path(__file__).resolve().parents[2] / "dev" / "realm" / "qaguru-realm.json"
ENV_FILE = Path(os.environ.get("AUTH_ENV", Path.home() / ".config/auth-qa-guru/keycloak.env"))
PLACEHOLDER = re.compile(r"^\$\{[A-Z0-9_]+\}$")
SECRET_KEYS = {"secret", "clientSecret", "value", "password"}
PILOT = {"svasenkov", "student-pilot"}
DEMO = {"student-demo", "mentor-demo", "staff-demo"}

checks: list[tuple[str, bool, str]] = []


def ssl_ctx() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, bool(ok), detail))


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def http_json(url: str, *, token: str | None = None, data: bytes | None = None, method: str = "GET") -> object:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def secrets_in_realm_file(path: Path) -> list[str]:
    leaks: list[str] = []

    def walk(node: object, cur: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in SECRET_KEYS and isinstance(value, str) and not PLACEHOLDER.match(value):
                    leaks.append(f"{cur}.{key}")
                walk(value, f"{cur}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{cur}[{i}]")

    walk(json.loads(path.read_text(encoding="utf-8")), "realm")
    return leaks


def main() -> int:
    env = load_env()
    oidc = http_json(f"{BASE}/realms/{REALM}/.well-known/openid-configuration")
    assert isinstance(oidc, dict)
    check("oidc issuer", oidc.get("issuer") == f"{BASE}/realms/{REALM}", str(oidc.get("issuer")))

    req = urllib.request.Request(f"{BASE}/realms/{REALM}/protocol/saml/descriptor")
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
        saml = resp.read().decode()
    check("saml descriptor", "EntityDescriptor" in saml and "auth.qa.guru" in saml)

    token_url = f"{BASE}/realms/master/protocol/openid-connect/token"
    form = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": env["KC_BOOTSTRAP_ADMIN_USERNAME"],
            "password": env["KC_BOOTSTRAP_ADMIN_PASSWORD"],
        }
    ).encode()
    token = http_json(token_url, data=form, method="POST")["access_token"]  # type: ignore[index]

    realm = http_json(f"{BASE}/admin/realms/{REALM}", token=token)
    assert isinstance(realm, dict)
    check("email is not a login", realm.get("loginWithEmailAllowed") is False)
    check("duplicate emails allowed", realm.get("duplicateEmailsAllowed") is True)
    check("users cannot rename themselves", realm.get("editUsernameAllowed") is False)
    check("passkeys enabled", realm.get("webAuthnPolicyPasswordlessPasskeysEnabled") is True)
    check("RP ID is qa.guru", realm.get("webAuthnPolicyPasswordlessRpId") == "qa.guru", str(realm.get("webAuthnPolicyPasswordlessRpId")))
    check("password fallback kept", realm.get("resetPasswordAllowed") is True)

    groups = http_json(f"{BASE}/admin/realms/{REALM}/groups", token=token)
    paths = {g["path"] for g in groups} if isinstance(groups, list) else set()
    check("people groups", {"/staff", "/mentors", "/students"} <= paths, ", ".join(sorted(paths)))

    clients = {
        c["clientId"]: c
        for c in http_json(f"{BASE}/admin/realms/{REALM}/clients", token=token)  # type: ignore[union-attr]
    }
    check("sonar speaks SAML", clients.get("sonar", {}).get("protocol") == "saml")
    for name in ("grafana", "jenkins", "gitlab", "testops"):
        check(f"{name} speaks OIDC", clients.get(name, {}).get("protocol") == "openid-connect")

    users = http_json(f"{BASE}/admin/realms/{REALM}/users?briefRepresentation=true&max=200", token=token)
    names = {u.get("username") for u in users} if isinstance(users, list) else set()
    check("no stand demo people", not (names & DEMO), ", ".join(sorted(names & DEMO)))
    unexpected = names - {"svc-provisioning"} - PILOT
    check("people are the P2b pilot only", not unexpected, ", ".join(sorted(unexpected)))

    leaks = secrets_in_realm_file(WRAPPER_REALM) if WRAPPER_REALM.is_file() else ["realm file missing"]
    check("no secrets in the tracked realm file", not leaks, ", ".join(leaks))

    width = max(len(n) for n, _, _ in checks)
    for name, ok, detail in checks:
        print(f"{'ok  ' if ok else 'FAIL'}  {name.ljust(width)}  {detail}".rstrip())
    failed = [n for n, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {exc.url} {exc.read()[:300]}", file=sys.stderr)
        raise
