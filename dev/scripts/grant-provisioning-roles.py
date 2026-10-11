#!/usr/bin/env python
"""Grant realm-management roles to svc-provisioning (P8).

The client is in realm JSON; the service-account user is created at import, so
the roles cannot live in the file. Idempotent.

    python scripts/grant-provisioning-roles.py                 # stand :8543
    python scripts/grant-provisioning-roles.py --prod          # https://auth.qa.guru
    python scripts/grant-provisioning-roles.py --prod --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

CLIENT_ID = "svc-provisioning"
ROLES = (
    "manage-users",
    "view-users",
    "query-users",
    "query-groups",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Grant realm-management roles to svc-provisioning")
    p.add_argument("--prod", action="store_true", help=f"Use {kc.PROD_ENV} against KC_HOSTNAME")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def client_by_id(env: dict[str, str], token: str, client_id: str) -> dict:
    found = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients?clientId={client_id}")
    if not found:
        raise SystemExit(f"client {client_id} missing in realm {kc.REALM}")
    return found[0]


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    plan = {
        "ok": True,
        "dry_run": ns.dry_run,
        "prod": ns.prod,
        "base_url": "https://auth.qa.guru" if ns.prod else "http://localhost:8543",
        "client": CLIENT_ID,
        "roles": list(ROLES),
    }
    if ns.dry_run:
        print(json.dumps(plan, ensure_ascii=False))
        return 0

    env = kc.load_env(kc.PROD_ENV if ns.prod else None)
    plan["base_url"] = kc.base_url(env)
    token = kc.admin_token(env)
    svc = client_by_id(env, token, CLIENT_ID)
    rm = client_by_id(env, token, "realm-management")
    sa = kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{svc['id']}/service-account-user")
    available = {
        r["name"]: r
        for r in kc.get(env, token, f"/admin/realms/{kc.REALM}/clients/{rm['id']}/roles")
    }
    missing_defs = [name for name in ROLES if name not in available]
    if missing_defs:
        raise SystemExit(f"realm-management roles missing: {missing_defs}")
    current = {
        r["name"]
        for r in kc.get(
            env,
            token,
            f"/admin/realms/{kc.REALM}/users/{sa['id']}/role-mappings/clients/{rm['id']}",
        )
        or []
    }
    to_add = [available[name] for name in ROLES if name not in current]
    if to_add:
        kc.post(
            env,
            token,
            f"/admin/realms/{kc.REALM}/users/{sa['id']}/role-mappings/clients/{rm['id']}",
            [{"id": r["id"], "name": r["name"]} for r in to_add],
        )
    action = "granted" if to_add else "already"
    print(
        json.dumps(
            {
                **plan,
                "service_account": sa.get("username"),
                "already": sorted(current),
                "added": [r["name"] for r in to_add],
                "action": action,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
