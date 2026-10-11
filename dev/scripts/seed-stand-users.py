#!/usr/bin/env python
"""Create demo people on the local stand. Idempotent.

Deliberately NOT part of realm/qaguru-realm.json: that file is the artifact P2 carries to the
prod VM, and a prod realm must come up with no people in it. Passwords come from dev/.env.

mentor-demo and student-demo share one email on purpose — the data room has 183 email
collisions, so the realm must accept them (duplicateEmailsAllowed, ADR 017 п. 5).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

PEOPLE = [
    {
        "username": "svasenkov",
        "firstName": "Stanislav",
        "lastName": "Vasenkov",
        "email": "svasenkov@qa.guru",
        "groups": ["/staff"],
    },
    {
        "username": "mentor-demo",
        "firstName": "Ментор",
        "lastName": "Демо",
        "email": "demo@qa.guru",
        "groups": ["/mentors"],
    },
    {
        "username": "student-demo",
        "firstName": "Студент",
        "lastName": "Демо",
        "email": "demo@qa.guru",
        "groups": ["/students"],
    },
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed or delete stand demo people")
    p.add_argument(
        "--delete-user",
        action="append",
        default=[],
        metavar="USERNAME",
        help="Delete a stand user (repeatable). Skips seeding when set.",
    )
    return p.parse_args(argv)


def delete_users(env: dict, token: str, usernames: list[str]) -> list[dict]:
    results = []
    for username in usernames:
        existing = kc.get(env, token, f"/admin/realms/{kc.REALM}/users?username={username}&exact=true")
        if not existing:
            results.append({"username": username, "action": "missing"})
            continue
        kc.delete(env, token, f"/admin/realms/{kc.REALM}/users/{existing[0]['id']}")
        results.append({"username": username, "id": existing[0]["id"], "action": "deleted"})
    return results


def main(argv: list[str] | None = None) -> None:
    ns = parse_args(argv)
    env = kc.load_env()
    token = kc.admin_token(env)
    if ns.delete_user:
        results = delete_users(env, token, ns.delete_user)
        print(json.dumps({"ok": True, "realm": kc.REALM, "users": results}, ensure_ascii=False, indent=2))
        return
    password = env.get("STAND_USER_PASSWORD")
    if not password:
        raise SystemExit("STAND_USER_PASSWORD missing in dev/.env")

    results = []
    for person in PEOPLE:
        username = person["username"]
        existing = kc.get(env, token, f"/admin/realms/{kc.REALM}/users?username={username}&exact=true")
        payload = {
            "username": username,
            "firstName": person["firstName"],
            "lastName": person["lastName"],
            "email": person["email"],
            "enabled": True,
            "emailVerified": True,
            "groups": person["groups"],
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        }
        if existing:
            user_id = existing[0]["id"]
            kc.put(env, token, f"/admin/realms/{kc.REALM}/users/{user_id}", payload)
            action = "updated"
        else:
            kc.post(env, token, f"/admin/realms/{kc.REALM}/users", payload)
            user_id = kc.get(
                env, token, f"/admin/realms/{kc.REALM}/users?username={username}&exact=true"
            )[0]["id"]
            action = "created"
        results.append({"username": username, "id": user_id, "groups": person["groups"], "action": action})

    print(json.dumps({"ok": True, "realm": kc.REALM, "users": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
