#!/usr/bin/env python
"""Push SMTP settings from dev/.env into the running realm.

Realm JSON keeps ${KC_SMTP_*} placeholders; Keycloak resolves those only at
--import-realm. Patching in place keeps passkeys and demo people. A later clean
import from the same .env lands on the same config.

Reset is by username. This script never enables loginWithEmailAllowed (ADR 017 §5)
and never mails more than one user.

    python scripts/apply-smtp.py
    python scripts/apply-smtp.py --send-reset student-demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

STAND_DEFAULTS = {
    "KC_SMTP_HOST": "mailpit",
    "KC_SMTP_PORT": "1025",
    "KC_SMTP_FROM": "keycloak@localhost",
    "KC_SMTP_FROM_DISPLAY": "QA Guru",
    "KC_SMTP_SSL": "false",
    "KC_SMTP_STARTTLS": "false",
    "KC_SMTP_AUTH": "false",
    "KC_SMTP_USER": "",
    "KC_SMTP_PASSWORD": "",
    "MAILPIT_UI_PORT": "8025",
}

SMTP_KEYS = (
    "host",
    "port",
    "from",
    "fromDisplayName",
    "ssl",
    "starttls",
    "auth",
    "user",
    "password",
)


def persist_missing_env(env: dict[str, str]) -> list[str]:
    """Append stand SMTP defaults to .env without touching existing keys."""
    env_file = kc.DEV_DIR / ".env"
    text = env_file.read_text(encoding="utf-8")
    added: list[str] = []
    for key, value in STAND_DEFAULTS.items():
        if key in env:
            continue
        if any(line.startswith(f"{key}=") for line in text.splitlines()):
            continue
        if not added:
            text = text.rstrip() + "\n\n# SMTP (stand Mailpit). Prod secrets live in keycloak.env 600.\n"
        if any(ch.isspace() for ch in value) and not (value.startswith('"') and value.endswith('"')):
            line = f'{key}="{value}"\n'
        else:
            line = f"{key}={value}\n"
        text += line
        env[key] = value
        added.append(key)
    if added:
        env_file.write_text(text, encoding="utf-8")
        env_file.chmod(0o600)
    return added


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def smtp_from_env(env: dict[str, str]) -> dict[str, str]:
    host = unquote(env.get("KC_SMTP_HOST", "") or STAND_DEFAULTS["KC_SMTP_HOST"])
    auth = unquote(env.get("KC_SMTP_AUTH") or STAND_DEFAULTS["KC_SMTP_AUTH"]).lower()
    password = unquote(env.get("KC_SMTP_PASSWORD", ""))
    if auth in {"true", "1", "yes"} and not password.strip():
        raise SystemExit("KC_SMTP_AUTH=true but KC_SMTP_PASSWORD is empty in dev/.env")
    return {
        "host": host,
        "port": unquote(env.get("KC_SMTP_PORT") or STAND_DEFAULTS["KC_SMTP_PORT"]),
        "from": unquote(env.get("KC_SMTP_FROM") or STAND_DEFAULTS["KC_SMTP_FROM"]),
        "fromDisplayName": unquote(env.get("KC_SMTP_FROM_DISPLAY") or STAND_DEFAULTS["KC_SMTP_FROM_DISPLAY"]),
        "ssl": unquote(env.get("KC_SMTP_SSL") or STAND_DEFAULTS["KC_SMTP_SSL"]),
        "starttls": unquote(env.get("KC_SMTP_STARTTLS") or STAND_DEFAULTS["KC_SMTP_STARTTLS"]),
        "auth": auth if auth in {"true", "false"} else "false",
        "user": unquote(env.get("KC_SMTP_USER", "")),
        "password": password,
    }


def public_smtp(smtp: dict[str, str]) -> dict[str, str]:
    out = {k: smtp.get(k, "") for k in SMTP_KEYS}
    out["password"] = "***" if smtp.get("password") else ""
    return out


def apply(env: dict[str, str], smtp: dict[str, str]) -> dict[str, object]:
    token = kc.admin_token(env)
    realm = kc.get(env, token, f"/admin/realms/{kc.REALM}")
    if realm.get("loginWithEmailAllowed") is not False:
        raise SystemExit("refusing: loginWithEmailAllowed is on — ADR 017 §5, email is not a login")
    if realm.get("resetPasswordAllowed") is not True:
        raise SystemExit("refusing: resetPasswordAllowed is off — the «Forgot password?» button would disappear")
    realm["smtpServer"] = smtp
    realm["loginWithEmailAllowed"] = False
    realm["registrationEmailAsUsername"] = False
    realm["duplicateEmailsAllowed"] = True
    realm["resetPasswordAllowed"] = True
    kc.put(env, token, f"/admin/realms/{kc.REALM}", realm)
    after = kc.get(env, token, f"/admin/realms/{kc.REALM}")
    if after.get("loginWithEmailAllowed") is not False:
        raise SystemExit("smtp applied but loginWithEmailAllowed flipped on — rolled? check the realm")
    return after


def send_reset(env: dict[str, str], username: str) -> dict[str, object]:
    if username.lower() in {"*", "all", "--all"} or "," in username:
        raise SystemExit("refusing: reset is one username at a time, never the whole realm")
    token = kc.admin_token(env)
    users = kc.get(env, token, f"/admin/realms/{kc.REALM}/users?username={username}&exact=true")
    if not users:
        raise SystemExit(f"user {username!r} not found — will not guess")
    if len(users) != 1:
        raise SystemExit(f"username {username!r} matched {len(users)} people — refusing")
    user = users[0]
    email = (user.get("email") or "").strip()
    if not email:
        raise SystemExit(f"{username} has no email attribute — nothing to send to")
    uid = user["id"]
    redirect = f"{kc.base_url(env)}/realms/{kc.REALM}/account/"
    path = (
        f"/admin/realms/{kc.REALM}/users/{uid}/execute-actions-email"
        f"?client_id=account-console&redirect_uri={redirect}&lifespan=3600"
    )
    kc.put(env, token, path, ["UPDATE_PASSWORD"])
    return {"username": username, "email": email, "action": "UPDATE_PASSWORD"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send-reset",
        metavar="USERNAME",
        help="mail UPDATE_PASSWORD to exactly one user (never the realm)",
    )
    args = parser.parse_args()

    env = kc.load_env()
    added = persist_missing_env(env)
    smtp = smtp_from_env(env)
    after = apply(env, smtp)
    result: dict[str, object] = {
        "ok": True,
        "smtp": public_smtp(after.get("smtpServer") or {}),
        "loginWithEmailAllowed": after.get("loginWithEmailAllowed"),
        "resetPasswordAllowed": after.get("resetPasswordAllowed"),
        "env_keys_appended": added,
    }
    if args.send_reset:
        result["reset"] = send_reset(env, args.send_reset.strip())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
