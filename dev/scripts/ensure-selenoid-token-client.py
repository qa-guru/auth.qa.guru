#!/usr/bin/env python
"""Create svc-selenoid-token (view-users) without touching svc-provisioning roles.

The client is in realm JSON; the service-account user and realm-management
roles cannot live in the file. Idempotent. Does not PUT the realm, User Profile,
or svc-provisioning.

    python scripts/ensure-selenoid-token-client.py
    python scripts/ensure-selenoid-token-client.py --prod
    python scripts/ensure-selenoid-token-client.py --prod --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

CLIENT_ID = "svc-selenoid-token"
PROVISIONING_ID = "svc-provisioning"
SECRET_KEY = "KC_CLIENT_SECRET_SELENOID_TOKEN"
PROD_SECRET_FILE = Path.home() / ".config" / "auth-qa-guru" / "selenoid-token.env"
ROLES = ("view-users", "query-users")
PROVISIONING_KEEP = ("manage-users", "view-users", "query-users", "query-groups")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ensure svc-selenoid-token with view-users only")
    p.add_argument("--prod", action="store_true", help=f"use {kc.PROD_ENV} (https://auth.qa.guru)")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def realm_client() -> dict[str, Any]:
    realm = json.loads((kc.DEV_DIR / "realm" / "qaguru-realm.json").read_text(encoding="utf-8"))
    for client in realm.get("clients") or []:
        if client.get("clientId") == CLIENT_ID:
            return client
    raise SystemExit(f"{CLIENT_ID} missing from realm/qaguru-realm.json")


def subst_secret(node: Any, secret: str) -> Any:
    if isinstance(node, dict):
        return {k: subst_secret(v, secret) for k, v in node.items()}
    if isinstance(node, list):
        return [subst_secret(v, secret) for v in node]
    if isinstance(node, str) and node == f"${{{SECRET_KEY}}}":
        return secret
    return node


def client_by_id(env: dict[str, str], token: str, client_id: str) -> dict[str, Any] | None:
    found = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients?clientId={client_id}")
    if not found:
        return None
    return found[0]


def load_kv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return kc.load_env(path)


def write_secret_line(path: Path, secret: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    env = load_kv(path) if path.is_file() else {}
    env[SECRET_KEY] = secret
    lines = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{SECRET_KEY}="):
                continue
            lines.append(line)
        if lines and lines[-1] != "":
            lines.append("")
    lines.append(f"{SECRET_KEY}={secret}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def ensure_secret(*, prod: bool) -> str:
    if prod:
        env = load_kv(PROD_SECRET_FILE)
        secret = (env.get(SECRET_KEY) or "").strip()
        if secret:
            return secret
        secret = secrets.token_urlsafe(24)
        write_secret_line(PROD_SECRET_FILE, secret)
        return secret
    env = kc.load_env()
    secret = (env.get(SECRET_KEY) or "").strip()
    if secret:
        return secret
    secret = secrets.token_urlsafe(24)
    write_secret_line(kc.DEV_DIR / ".env", secret)
    return secret


def role_names(env: dict[str, str], token: str, user_id: str, rm_id: str) -> set[str]:
    current = (
        kc.get(
            env,
            token,
            f"/admin/realms/{kc.REALM}/users/{user_id}/role-mappings/clients/{rm_id}",
        )
        or []
    )
    return {r["name"] for r in current}


def grant_roles(
    env: dict[str, str],
    token: str,
    sa_id: str,
    rm_id: str,
    available: dict[str, dict[str, Any]],
    names: tuple[str, ...],
) -> list[str]:
    current = role_names(env, token, sa_id, rm_id)
    missing = [name for name in names if name not in available]
    if missing:
        raise SystemExit(f"realm-management roles missing: {missing}")
    to_add = [available[name] for name in names if name not in current]
    if to_add:
        kc.post(
            env,
            token,
            f"/admin/realms/{kc.REALM}/users/{sa_id}/role-mappings/clients/{rm_id}",
            [{"id": r["id"], "name": r["name"]} for r in to_add],
        )
    return [r["name"] for r in to_add]


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    wanted = realm_client()
    plan = {
        "ok": True,
        "dry_run": ns.dry_run,
        "prod": ns.prod,
        "client": CLIENT_ID,
        "roles": list(ROLES),
        "not_touching": PROVISIONING_ID,
    }
    if ns.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    env = kc.load_env(kc.PROD_ENV if ns.prod else None)
    if ns.prod:
        env.setdefault("KC_HOSTNAME", "https://auth.qa.guru")
    secret = ensure_secret(prod=ns.prod)
    payload = subst_secret(wanted, secret)
    token = kc.admin_token(env)
    provisioning = client_by_id(env, token, PROVISIONING_ID)
    if provisioning is None:
        raise SystemExit(f"{PROVISIONING_ID} missing — abort")
    rm = client_by_id(env, token, "realm-management")
    if rm is None:
        raise SystemExit("realm-management client missing")
    provisioning_sa = kc.get(
        env,
        token,
        f"/admin/realms/{kc.REALM}/clients/{provisioning['id']}/service-account-user",
    )
    before_provisioning = role_names(env, token, provisioning_sa["id"], rm["id"])
    missing_keep = [name for name in PROVISIONING_KEEP if name not in before_provisioning]
    if missing_keep:
        raise SystemExit(f"{PROVISIONING_ID} roles already missing {missing_keep} — abort")

    live = client_by_id(env, token, CLIENT_ID)
    if live is None:
        kc.post(env, token, f"/admin/realms/{kc.REALM}/clients", payload)
        action = "created"
        live = client_by_id(env, token, CLIENT_ID)
        if live is None:
            raise SystemExit(f"{CLIENT_ID} missing after create")
    else:
        uuid = live["id"]
        current = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}")
        current["name"] = payload["name"]
        current["description"] = payload["description"]
        current["publicClient"] = False
        current["standardFlowEnabled"] = False
        current["implicitFlowEnabled"] = False
        current["directAccessGrantsEnabled"] = False
        current["serviceAccountsEnabled"] = True
        current["secret"] = secret
        kc.put(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}", current)
        action = "updated"
        live = current

    sa = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{live['id']}/service-account-user")
    available = {
        r["name"]: r
        for r in kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{rm['id']}/roles")
    }
    added = grant_roles(env, token, sa["id"], rm["id"], available, ROLES)
    after_provisioning = role_names(env, token, provisioning_sa["id"], rm["id"])
    if after_provisioning != before_provisioning:
        raise SystemExit(
            f"{PROVISIONING_ID} roles changed {sorted(before_provisioning)} → "
            f"{sorted(after_provisioning)} — abort"
        )
    extra = role_names(env, token, sa["id"], rm["id"]) - set(ROLES)
    forbidden = extra & {"manage-users", "manage-clients", "manage-realm", "realm-admin"}
    if forbidden:
        raise SystemExit(f"{CLIENT_ID} has extra admin roles {sorted(forbidden)} — abort")

    print(
        json.dumps(
            {
                **plan,
                "action": action,
                "base_url": kc.base_url(env),
                "service_account": sa.get("username"),
                "added_roles": added,
                "roles_now": sorted(role_names(env, token, sa["id"], rm["id"])),
                "provisioning_roles": sorted(after_provisioning),
                "secret": "set",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
