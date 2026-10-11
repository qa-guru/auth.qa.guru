#!/usr/bin/env python
"""Dump what the running server actually holds, to diff against the hand-written realm file.

Uses the partial-export endpoint rather than `kc.sh export`: the CLI export boots a second JVM
that fails on the management port already taken by the running server, and the endpoint masks
client secrets on the way out.

The dump is NOT the source of truth — it carries several hundred lines of default flows and
masked secrets. Read the diff, port the meaningful part back into realm/qaguru-realm.json by hand.

    python scripts/export-realm.py            # stand :8543
    python scripts/export-realm.py --prod     # https://auth.qa.guru
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prod", action="store_true", help=f"Use {kc.PROD_ENV} against KC_HOSTNAME")
    return p.parse_args(argv)


def export_realm(env: dict[str, str]) -> dict:
    token = kc.admin_token(env)
    return kc.post(
        env,
        token,
        f"/admin/realms/{kc.REALM}/partial-export?exportGroupsAndRoles=true&exportClients=true",
        {},
    )


def main(argv: list[str] | None = None) -> None:
    ns = parse_args(argv)
    env = kc.load_env(kc.PROD_ENV if ns.prod else None)
    realm = export_realm(env)
    out_dir = kc.DEV_DIR / "export"
    out_dir.mkdir(exist_ok=True)
    name = f"{kc.REALM}-realm-prod.json" if ns.prod else f"{kc.REALM}-realm.json"
    out = out_dir / name
    out.write_text(json.dumps(realm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "prod": ns.prod,
                "base_url": kc.base_url(env),
                "file": str(out),
                "clients": len(realm.get("clients") or []),
                "groups": len(realm.get("groups") or []),
                "note": "gitignored; secrets masked; source of truth stays realm/qaguru-realm.json",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
