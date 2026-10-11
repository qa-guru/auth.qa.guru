#!/usr/bin/env python
"""Merge selenoidToken into the live User Profile. Does not PUT the realm, clients, or users.

Import does not carry User Profile. Live stand/prod need this after the extras file is added.

    python scripts/apply-user-profile.py
    python scripts/apply-user-profile.py --prod
    python scripts/apply-user-profile.py --prod --verify-only
    python scripts/apply-user-profile.py --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

EXTRAS_FILE = kc.DEV_DIR / "realm" / "user-profile-selenoid-token.json"
KEEP = ("username", "email", "firstName", "lastName")
TOKEN_NAME = "selenoidToken"
TOKEN_GROUP = "selenoid"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prod", action="store_true", help=f"use {kc.PROD_ENV} (https://auth.qa.guru)")
    p.add_argument("--verify-only", action="store_true", help="GET profile, do not PUT")
    p.add_argument("--dry-run", action="store_true", help="print merged profile, do not PUT")
    return p.parse_args(argv)


def extras() -> dict[str, Any]:
    return json.loads(EXTRAS_FILE.read_text(encoding="utf-8"))


def merge(live: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(live)
    groups = list(out.get("groups") or [])
    group_idx = {g.get("name"): i for i, g in enumerate(groups)}
    for group in extra.get("groups") or []:
        name = group["name"]
        if name in group_idx:
            groups[group_idx[name]] = {**groups[group_idx[name]], **group}
        else:
            groups.append(group)
    out["groups"] = groups

    attrs = list(out.get("attributes") or [])
    attr_idx = {a.get("name"): i for i, a in enumerate(attrs)}
    for attr in extra.get("attributes") or []:
        name = attr["name"]
        if name in attr_idx:
            attrs[attr_idx[name]] = copy.deepcopy(attr)
        else:
            attrs.append(copy.deepcopy(attr))
    names = {a.get("name") for a in attrs}
    missing = [n for n in KEEP if n not in names]
    if missing:
        raise SystemExit(f"refusing: merge would drop {missing}")
    before_names = {a.get("name") for a in (live.get("attributes") or [])}
    dropped = before_names - names
    if dropped:
        raise SystemExit(f"refusing: merge would drop existing attributes {sorted(dropped)}")
    out["attributes"] = attrs
    return out


def token_view(profile: dict[str, Any]) -> dict[str, Any]:
    attr = next((a for a in (profile.get("attributes") or []) if a.get("name") == TOKEN_NAME), None)
    groups = [g.get("name") for g in (profile.get("groups") or [])]
    if not attr:
        return {"present": False, "groups": groups}
    length = (attr.get("validations") or {}).get("length") or {}
    pattern = (attr.get("validations") or {}).get("pattern") or {}
    perms = attr.get("permissions") or {}
    return {
        "present": True,
        "group": attr.get("group"),
        "groups": groups,
        "view": perms.get("view") or [],
        "edit": perms.get("edit") or [],
        "length_min": length.get("min"),
        "length_max": length.get("max"),
        "pattern": pattern.get("pattern"),
        "unmanagedAttributePolicy": profile.get("unmanagedAttributePolicy"),
    }


def profile_ok(view: dict[str, Any]) -> bool:
    return (
        view.get("present") is True
        and view.get("group") == TOKEN_GROUP
        and TOKEN_GROUP in (view.get("groups") or [])
        and "user" in (view.get("view") or [])
        and "user" in (view.get("edit") or [])
        and "admin" in (view.get("edit") or [])
        and view.get("length_min") == 20
        and view.get("pattern") == r"^\S+$"
    )


def extras_applied(live: dict[str, Any], extra: dict[str, Any]) -> bool:
    if not profile_ok(token_view(live)):
        return False
    live_groups = {g.get("name"): g for g in (live.get("groups") or [])}
    for group in extra.get("groups") or []:
        have = live_groups.get(group["name"]) or {}
        if have.get("displayHeader") != group.get("displayHeader"):
            return False
        if have.get("displayDescription") != group.get("displayDescription"):
            return False
    live_attrs = {a.get("name"): a for a in (live.get("attributes") or [])}
    for attr in extra.get("attributes") or []:
        have = live_attrs.get(attr["name"]) or {}
        if have.get("displayName") != attr.get("displayName"):
            return False
        if have.get("annotations") != attr.get("annotations"):
            return False
        if have.get("validations") != attr.get("validations"):
            return False
        if have.get("permissions") != attr.get("permissions"):
            return False
        if have.get("group") != attr.get("group"):
            return False
    return True


def users_count(env: dict[str, str], token: str) -> int:
    n = kc.get(env, token, f"/admin/realms/{kc.REALM}/users/count")
    return int(n)


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    extra = extras()
    if ns.dry_run and not ns.prod and not ns.verify_only:
        print(json.dumps({"ok": True, "dry_run": True, "extras": extra}, ensure_ascii=False, indent=2))
        return 0

    env = kc.load_env(kc.PROD_ENV if ns.prod else None)
    if ns.prod:
        env.setdefault("KC_HOSTNAME", "https://auth.qa.guru")
    token = kc.admin_token(env)
    live = kc.get(env, token, f"/admin/realms/{kc.REALM}/users/profile") or {}
    merged = merge(live, extra)
    result: dict[str, Any] = {
        "ok": True,
        "prod": ns.prod,
        "base_url": kc.base_url(env),
        "unmanagedAttributePolicy": live.get("unmanagedAttributePolicy"),
    }
    if ns.verify_only:
        view = token_view(live)
        result["action"] = "verify"
        result["selenoidToken"] = view
        result["ok"] = extras_applied(live, extra)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if ns.dry_run:
        result["action"] = "dry-run"
        result["selenoidToken"] = token_view(merged)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    before_clients = kc.client_ids(env, token, kc.REALM)
    before_users = users_count(env, token)
    before_names = [a.get("name") for a in (live.get("attributes") or [])]
    if extras_applied(live, extra):
        result["action"] = "already"
        result["attributes"] = before_names
        result["selenoidToken"] = token_view(live)
        result["users"] = before_users
        result["clients"] = len(before_clients)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    kc.put(env, token, f"/admin/realms/{kc.REALM}/users/profile", merged)
    after = kc.get(env, token, f"/admin/realms/{kc.REALM}/users/profile") or {}
    after_clients = kc.client_ids(env, token, kc.REALM)
    after_users = users_count(env, token)
    if after_clients != before_clients:
        raise SystemExit("clients changed after user-profile PUT — abort")
    if after_users != before_users:
        raise SystemExit(f"user count changed {before_users} → {after_users} — abort")
    view = token_view(after)
    after_names = [a.get("name") for a in (after.get("attributes") or [])]
    result["action"] = "created" if TOKEN_NAME not in before_names else "updated"
    result["attributes"] = after_names
    result["selenoidToken"] = view
    result["users"] = after_users
    result["clients"] = len(after_clients)
    result["ok"] = extras_applied(after, extra) and after.get("unmanagedAttributePolicy") == live.get(
        "unmanagedAttributePolicy"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
