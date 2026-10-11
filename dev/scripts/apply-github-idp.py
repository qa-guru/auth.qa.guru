#!/usr/bin/env python
"""Push the GitHub OAuth App credentials from dev/.env into the running realm.

The realm file carries ${KC_GITHUB_CLIENT_ID} / ${KC_GITHUB_CLIENT_SECRET} placeholders, but
Keycloak resolves those only at --import-realm. Rather than wipe the stand (and its registered
passkeys) just to change two strings, patch the identity provider in place — a later clean
import from the same .env lands on exactly the same config.

    python scripts/apply-github-idp.py
    python scripts/apply-github-idp.py --prod   # mapper only; does not touch OAuth secrets
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

STUDENTS_MAPPER = {
    "name": "github-to-students",
    "identityProviderAlias": "github",
    "identityProviderMapper": "oidc-hardcoded-group-idp-mapper",
    "config": {
        "syncMode": "FORCE",
        "group": "/students",
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply GitHub IdP credentials and /students mapper")
    p.add_argument("--prod", action="store_true", help=f"Mapper only, via {kc.PROD_ENV}")
    return p.parse_args(argv)


def ensure_students_mapper(env: dict[str, str], token: str) -> str:
    path = f"/admin/realms/{kc.REALM}/identity-provider/instances/github/mappers"
    existing = kc.get(env, token, path) or []
    found = next((m for m in existing if m.get("name") == STUDENTS_MAPPER["name"]), None)
    if found:
        body = {
            **STUDENTS_MAPPER,
            "id": found["id"],
            "identityProviderAlias": found.get("identityProviderAlias") or "github",
        }
        kc.put(env, token, f"{path}/{found['id']}", body)
        return "updated"
    kc.post(env, token, path, STUDENTS_MAPPER)
    return "created"


def main(argv: list[str] | None = None) -> None:
    ns = parse_args(argv)
    if ns.prod:
        env = kc.load_env(kc.PROD_ENV)
        token = kc.admin_token(env)
        mapper = ensure_students_mapper(env, token)
        print(
            json.dumps(
                {
                    "ok": True,
                    "prod": True,
                    "base_url": kc.base_url(env),
                    "mapper": STUDENTS_MAPPER["name"],
                    "group": "/students",
                    "action": mapper,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    env = kc.load_env()
    client_id = env.get("KC_GITHUB_CLIENT_ID", "").strip()
    client_secret = env.get("KC_GITHUB_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            "KC_GITHUB_CLIENT_ID / KC_GITHUB_CLIENT_SECRET are empty in dev/.env — "
            "create your own test OAuth App first (see dev/README.md § GitHub brokering)"
        )

    token = kc.admin_token(env)
    idp = kc.get(env, token, f"/admin/realms/{kc.REALM}/identity-provider/instances/github")
    # The realm file ships the provider disabled: an enabled button with placeholder
    # credentials is a dead end for everyone on the login page. Real credentials turn it on.
    idp["enabled"] = True
    idp["config"]["clientId"] = client_id
    idp["config"]["clientSecret"] = client_secret
    kc.put(env, token, f"/admin/realms/{kc.REALM}/identity-provider/instances/github", idp)
    mapper = ensure_students_mapper(env, token)

    callback = f"{kc.base_url(env)}/realms/{kc.REALM}/broker/github/endpoint"
    print(
        json.dumps(
            {
                "ok": True,
                "clientId": client_id,
                "callback": callback,
                "mapper": mapper,
                "next": f"sign in with GitHub at {kc.base_url(env)}/realms/{kc.REALM}/account/, "
                "then run python scripts/verify-realm.py",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
