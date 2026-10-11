#!/usr/bin/env python
"""Confidential OIDC client autotests-ai for dest cloud. Not learn. Secret never printed.

    python scripts/ensure-autotests-ai-client.py
    python scripts/ensure-autotests-ai-client.py --prod
    python scripts/ensure-autotests-ai-client.py --dry-run

Stand secret: dev/.env (KC_CLIENT_SECRET_AUTOTESTS_AI), mode 600.
Prod secret: ~/.config/auth-qa-guru/autotests-ai.env (600). Home copies IDP_CLIENT_SECRET from there.
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

CLIENT_ID = "autotests-ai"
LEARN_ID = "learn"
SECRET_KEY = "KC_CLIENT_SECRET_AUTOTESTS_AI"
PROD_SECRET_FILE = Path.home() / ".config" / "auth-qa-guru" / "autotests-ai.env"
PROD_IDP = "https://auth.qa.guru/realms/qaguru/protocol/openid-connect"
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
        "multivalued": "true",
    },
}
USERNAME_MAPPER = {
    "name": "preferred_username",
    "protocol": "openid-connect",
    "protocolMapper": "oidc-usermodel-property-mapper",
    "config": {
        "user.attribute": "username",
        "claim.name": "preferred_username",
        "id.token.claim": "true",
        "access.token.claim": "true",
        "introspection.token.claim": "true",
        "userinfo.token.claim": "true",
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ensure autotests-ai OIDC client for dest cloud")
    p.add_argument("--prod", action="store_true", help=f"use {kc.PROD_ENV} (https://auth.qa.guru)")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def realm_client() -> dict[str, Any]:
    realm = json.loads((kc.DEV_DIR / "realm" / "qaguru-realm.json").read_text(encoding="utf-8"))
    found = None
    for client in realm.get("clients") or []:
        if client.get("clientId") == CLIENT_ID:
            found = client
            break
    if found is None:
        raise SystemExit(f"{CLIENT_ID} missing from realm/qaguru-realm.json")
    if found.get("clientId") == LEARN_ID:
        raise SystemExit("refusing to touch client learn")
    return found


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
    return found[0] if found else None


def load_kv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return kc.load_env(path)


def write_prod_secret_file(secret: str) -> None:
    path = PROD_SECRET_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    env = load_kv(path) if path.is_file() else {}
    token_url = env.get("IDP_TOKEN_URL") or f"{PROD_IDP}/token"
    userinfo_url = env.get("IDP_USERINFO_URL") or f"{PROD_IDP}/userinfo"
    keep = {SECRET_KEY, "IDP_CLIENT_ID", "IDP_CLIENT_SECRET", "IDP_TOKEN_URL", "IDP_USERINFO_URL"}
    lines: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            key = stripped.split("=", 1)[0]
            if key in keep:
                continue
            lines.append(line)
        if lines and lines[-1] != "":
            lines.append("")
    lines.extend(
        [
            f"{SECRET_KEY}={secret}",
            f"IDP_CLIENT_ID={CLIENT_ID}",
            f"IDP_CLIENT_SECRET={secret}",
            f"IDP_TOKEN_URL={token_url}",
            f"IDP_USERINFO_URL={userinfo_url}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def write_stand_secret_line(secret: str) -> None:
    path = kc.DEV_DIR / ".env"
    previous = path.read_text(encoding="utf-8") if path.is_file() else ""
    suffix = "" if previous.endswith("\n") or not previous else "\n"
    path.write_text(previous + suffix + f"{SECRET_KEY}={secret}\n", encoding="utf-8")
    os.chmod(path, 0o600)


def ensure_secret(*, prod: bool) -> str:
    if prod:
        env = load_kv(PROD_SECRET_FILE)
        secret = (env.get(SECRET_KEY) or env.get("IDP_CLIENT_SECRET") or "").strip()
        if not secret:
            secret = secrets.token_urlsafe(24)
        write_prod_secret_file(secret)
        return secret
    env = kc.load_env()
    secret = (env.get(SECRET_KEY) or "").strip()
    if secret:
        return secret
    secret = secrets.token_urlsafe(24)
    write_stand_secret_line(secret)
    return secret


def ensure_mappers(env: dict[str, str], token: str, uuid: str) -> None:
    mappers = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}/protocol-mappers/models") or []
    names = {m.get("name") for m in mappers}
    if "preferred_username" not in names:
        kc.post(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}/protocol-mappers/models", USERNAME_MAPPER)
    if "groups" not in names:
        kc.post(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}/protocol-mappers/models", GROUPS_MAPPER)


def apply_client(*, prod: bool, dry_run: bool) -> int:
    wanted = realm_client()
    plan = {
        "ok": True,
        "dry_run": dry_run,
        "prod": prod,
        "client": CLIENT_ID,
        "not_touching": LEARN_ID,
        "publicClient": False,
        "redirectUris": wanted.get("redirectUris"),
        "secret": "not-printed",
        "home_env": str(PROD_SECRET_FILE) if prod else str(kc.DEV_DIR / ".env"),
        "home_names": [
            "VITE_IDP_AUTHORIZE_URL",
            "VITE_IDP_CLIENT_ID",
            "IDP_CLIENT_ID",
            "IDP_CLIENT_SECRET",
            "IDP_TOKEN_URL",
            "IDP_USERINFO_URL",
        ],
    }
    if dry_run:
        print(json.dumps(plan, ensure_ascii=False))
        return 0

    if prod:
        env = dict(kc.load_env(kc.PROD_ENV))
        env.setdefault("KC_HOSTNAME", "https://auth.qa.guru")
        env[SECRET_KEY] = ensure_secret(prod=True)
    else:
        env = dict(kc.load_env())
        env[SECRET_KEY] = ensure_secret(prod=False)

    payload = subst_secret(wanted, env[SECRET_KEY])
    token = kc.admin_token(env)
    live = client_by_id(env, token, CLIENT_ID)
    if live is None:
        kc.post(env, token, f"/admin/realms/{kc.REALM}/clients", payload)
        action = "created"
        live = client_by_id(env, token, CLIENT_ID)
        if live is None:
            raise SystemExit(f"{CLIENT_ID} missing after create")
        ensure_mappers(env, token, live["id"])
    else:
        uuid = live["id"]
        current = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}")
        if current.get("clientId") == LEARN_ID:
            raise SystemExit("refusing: live client is learn")
        current["redirectUris"] = sorted(set(current.get("redirectUris") or []) | set(payload["redirectUris"]))
        current["webOrigins"] = sorted(set(current.get("webOrigins") or []) | set(payload["webOrigins"]))
        attrs = current.setdefault("attributes", {})
        attrs.pop("pkce.code.challenge.method", None)
        attrs["post.logout.redirect.uris"] = payload["attributes"]["post.logout.redirect.uris"]
        current["name"] = payload["name"]
        current["description"] = payload["description"]
        current["publicClient"] = False
        current["standardFlowEnabled"] = True
        current["implicitFlowEnabled"] = False
        current["directAccessGrantsEnabled"] = False
        current["serviceAccountsEnabled"] = False
        current["secret"] = env[SECRET_KEY]
        current["rootUrl"] = payload.get("rootUrl")
        current["baseUrl"] = payload.get("baseUrl")
        kc.put(env, token, f"/admin/realms/{kc.REALM}/clients/{uuid}", current)
        ensure_mappers(env, token, uuid)
        action = "updated"
        live = current

    print(
        json.dumps(
            {
                **plan,
                "action": action,
                "base_url": kc.base_url(env),
                "redirectUris": live.get("redirectUris"),
                "webOrigins": live.get("webOrigins"),
                "publicClient": live.get("publicClient"),
                "secret": "set",
            },
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    return apply_client(prod=ns.prod, dry_run=ns.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
