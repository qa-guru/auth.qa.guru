#!/usr/bin/env python
"""Set SSO session timeouts from qaguru-realm.json. Does not PUT clients. Does not stop Keycloak.

Defaults were 30 min idle / 10 h max, and Remember Me inherited those (0 = inherit).
School SSO: 2 d idle / 7 d max; Remember Me 7 d idle / 14 d max.

    python scripts/apply-session.py
    python scripts/apply-session.py --prod
    python scripts/apply-session.py --prod --verify-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

REALM_FILE = kc.DEV_DIR / "realm" / "qaguru-realm.json"
SESSION_KEYS = (
    "ssoSessionIdleTimeout",
    "ssoSessionMaxLifespan",
    "ssoSessionIdleTimeoutRememberMe",
    "ssoSessionMaxLifespanRememberMe",
)


def wanted_from_realm_file() -> dict[str, int]:
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    missing = [key for key in SESSION_KEYS if key not in realm]
    if missing:
        raise SystemExit(f"missing {missing} in {REALM_FILE}")
    out: dict[str, int] = {}
    for key in SESSION_KEYS:
        value = realm[key]
        if not isinstance(value, int) or value < 0:
            raise SystemExit(f"{key} must be a non-negative int of seconds, got {value!r}")
        out[key] = value
    return out


def view(realm: dict[str, Any]) -> dict[str, Any]:
    return {key: realm.get(key) for key in SESSION_KEYS}


def matches(live: dict[str, Any], wanted: dict[str, int]) -> bool:
    return all(live.get(key) == wanted[key] for key in SESSION_KEYS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prod", action="store_true", help=f"use {kc.PROD_ENV} (https://auth.qa.guru)")
    parser.add_argument("--verify-only", action="store_true", help="do not PUT realm, only check timeouts")
    args = parser.parse_args()

    wanted = wanted_from_realm_file()
    env = kc.load_env(kc.PROD_ENV if args.prod else None)
    if args.prod:
        env.setdefault("KC_HOSTNAME", "https://auth.qa.guru")
    token = kc.admin_token(env)
    result: dict[str, Any] = {
        "ok": True,
        "base": kc.base_url(env),
        "wanted": wanted,
    }

    if args.verify_only:
        school = kc.get(env, token, "/admin/realms/qaguru")
        master = kc.get(env, token, "/admin/realms/master")
        result["qaguru"] = view(school)
        result["master"] = view(master)
        result["ok"] = matches(school, wanted) and matches(master, wanted)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["ok"]:
            sys.exit(1)
        return

    result["qaguru"] = kc.put_realm_fields(env, token, "qaguru", wanted, school_guards=True)
    result["master"] = kc.put_realm_fields(env, token, "master", wanted, school_guards=False)
    result["ok"] = matches(result["qaguru"], wanted) and matches(result["master"], wanted)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
