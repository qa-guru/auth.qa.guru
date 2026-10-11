#!/usr/bin/env python
"""Set loginTheme=qaguru on qaguru; master stays stock keycloak.v2. Does not PUT clients. Does not stop Keycloak.

    python scripts/apply-theme.py
    python scripts/apply-theme.py --prod
    python scripts/apply-theme.py --prod --verify-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

THEME = "qaguru"
MASTER_LOGIN_THEME = "keycloak.v2"
PKCE = "code_challenge_method=S256&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
SCHOOL_NEEDLES = (
    'id="kc-form-login"',
    'id="username"',
    'id="password"',
    'id="kc-login"',
    'id="authenticateWebAuthnButton"',
    "broker/github/login",
)
ADMIN_NEEDLES = (
    'id="kc-form-login"',
    'id="username"',
    'id="password"',
    'id="kc-login"',
)
LIVE_STAFF = "svasenkov"


def login_url(base: str, *, realm: str, client_id: str, redirect: str) -> str:
    return (
        f"{base}/realms/{realm}/protocol/openid-connect/auth"
        f"?client_id={client_id}&redirect_uri={urllib.parse.quote(redirect, safe='')}"
        f"&response_type=code&scope=openid&{PKCE}"
    )


def fetch_login_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"Accept": "text/html", "User-Agent": "qa-guru-theme-check"})
    with urllib.request.urlopen(req, context=kc.CTX, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_login_html_via_vm(url: str) -> str:
    cmd = f"curl -sS --fail --max-time 30 -H 'Accept: text/html' {shlex.quote(url)}"
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "auth-qa-guru", cmd], capture_output=True, check=True, timeout=60
    )
    return proc.stdout.decode("utf-8", "replace")


def verify_html(html: str, needles: tuple[str, ...], *, themed: bool = True) -> dict[str, Any]:
    missing = [n for n in needles if n not in html]
    css_ok = f"/login/{THEME}/" in html and "css/login.css" in html
    staff_leak = LIVE_STAFF in html.lower()
    ds_header = 'data-testid="header"' in html
    header_css = "css/header.css" in html and "css/shell.css" in html
    stock = f"/login/{MASTER_LOGIN_THEME}/" in html
    look = (css_ok and ds_header and header_css) if themed else (stock and not (css_ok or ds_header or header_css))
    return {
        "css": css_ok,
        "ds_header": ds_header,
        "header_css": header_css,
        "stock": stock,
        "form_missing": missing,
        "len": len(html),
        "sha256": hashlib.sha256(html.encode()).hexdigest()[:16],
        "svasenkov_in_html": staff_leak,
        "ok": look and not missing and not staff_leak,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prod", action="store_true", help=f"use {kc.PROD_ENV} (https://auth.qa.guru)")
    parser.add_argument("--verify-only", action="store_true", help="do not PUT realm, only check login HTML")
    args = parser.parse_args()

    env_file = kc.PROD_ENV if args.prod else None
    env = kc.load_env(env_file)
    if args.prod:
        env.setdefault("KC_HOSTNAME", "https://auth.qa.guru")
    base = kc.base_url(env)

    token = kc.admin_token(env)
    result: dict[str, Any] = {"ok": True, "base": base, "theme": THEME}
    if not args.verify_only:
        result["qaguru"] = kc.put_login_theme(env, token, "qaguru", THEME, school_guards=True)
        result["master"] = kc.put_login_theme(env, token, "master", MASTER_LOGIN_THEME, school_guards=False)
    else:
        school = kc.get(env, token, "/admin/realms/qaguru")
        master = kc.get(env, token, "/admin/realms/master")
        result["qaguru"] = {"loginTheme": school.get("loginTheme")}
        result["master"] = {"loginTheme": master.get("loginTheme")}
    school_url = login_url(
        base, realm="qaguru", client_id="account-console", redirect=f"{base}/realms/qaguru/account/"
    )
    admin_url = login_url(
        base, realm="master", client_id="security-admin-console", redirect=f"{base}/admin/master/console/"
    )
    result["html"] = verify_html(fetch_login_html(school_url), SCHOOL_NEEDLES)
    admin_fetch = fetch_login_html_via_vm if args.prod else fetch_login_html
    result["admin_html"] = verify_html(admin_fetch(admin_url), ADMIN_NEEDLES, themed=False)
    if not result["html"]["ok"] or not result["admin_html"]["ok"]:
        result["ok"] = False
    if result.get("qaguru", {}).get("loginTheme") != THEME or result.get("master", {}).get("loginTheme") != MASTER_LOGIN_THEME:
        result["ok"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
