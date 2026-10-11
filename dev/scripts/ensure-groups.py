#!/usr/bin/env python
"""Create missing realm groups from qaguru-realm.json on a running Keycloak.

Import only fills groups on a fresh volume. Live stand/prod need this after a
new group is added to the file.

    python scripts/ensure-groups.py                 # stand :8543
    python scripts/ensure-groups.py --prod          # https://auth.qa.guru
    python scripts/ensure-groups.py --prod --prune-streams
    python scripts/ensure-groups.py --prod --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

REALM_FILE = kc.DEV_DIR / "realm" / "qaguru-realm.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ensure realm JSON groups exist in the running realm")
    p.add_argument("--prod", action="store_true", help=f"Use {kc.PROD_ENV} against KC_HOSTNAME")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--prune-streams",
        action="store_true",
        help="delete live /stream-* groups that are not in realm JSON",
    )
    return p.parse_args(argv)


def wanted_groups() -> list[dict]:
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    groups = realm.get("groups") or []
    if not groups:
        raise SystemExit(f"no groups in {REALM_FILE}")
    return groups


def live_paths(env: dict[str, str], token: str) -> set[str]:
    rows = kc.get(env, token, f"/admin/realms/{kc.REALM}/groups") or []
    return {g.get("path") or "" for g in rows}


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    wanted = wanted_groups()
    plan = {
        "ok": True,
        "dry_run": ns.dry_run,
        "prod": ns.prod,
        "base_url": "https://auth.qa.guru" if ns.prod else "http://localhost:8543",
        "wanted": [g.get("path") for g in wanted],
    }
    if ns.dry_run:
        print(json.dumps(plan, ensure_ascii=False))
        return 0

    env = kc.load_env(kc.PROD_ENV if ns.prod else None)
    plan["base_url"] = kc.base_url(env)
    token = kc.admin_token(env)
    have = live_paths(env, token)
    created: list[str] = []
    for group in wanted:
        path = group.get("path") or f"/{group['name']}"
        if path in have:
            continue
        kc.post(env, token, f"/admin/realms/{kc.REALM}/groups", {"name": group["name"]})
        created.append(path)
    pruned: list[str] = []
    if ns.prune_streams:
        wanted_paths = {g.get("path") or f"/{g['name']}" for g in wanted}
        live_rows = kc.get(env, token, f"/admin/realms/{kc.REALM}/groups") or []
        for row in live_rows:
            path = row.get("path") or ""
            gid = row.get("id") or ""
            if not path.startswith("/stream-") or path in wanted_paths or not gid:
                continue
            kc.delete(env, token, f"/admin/realms/{kc.REALM}/groups/{gid}")
            pruned.append(path)
    have_after = live_paths(env, token)
    missing = [g.get("path") for g in wanted if g.get("path") not in have_after]
    if missing:
        raise SystemExit(f"groups still missing after create: {missing}")
    leftover_streams = sorted(p for p in have_after if p.startswith("/stream-"))
    if ns.prune_streams and leftover_streams:
        raise SystemExit(f"stream groups still live: {leftover_streams}")
    print(
        json.dumps(
            {
                **plan,
                "already": sorted(have),
                "created": created,
                "pruned": pruned,
                "action": "created" if created else "already",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
