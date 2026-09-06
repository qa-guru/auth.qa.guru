#!/usr/bin/env python3
"""Prod SMTP for realm qaguru — password reset by username, never login-with-email.

Secrets: ~/.config/auth-qa-guru/keycloak.env + smtp.env (mode 600). Host copy
/etc/keycloak/keycloak.env root 600. Not Vault (ADR 017 §12).

  python3 deploy/smtp.py apply
  python3 deploy/smtp.py test --email aanher@gmail.com
  python3 deploy/smtp.py probe-reset
  python3 deploy/smtp.py send-reset USERNAME   # exactly one person, never the realm
  python3 deploy/smtp.py status
"""
from __future__ import annotations

import argparse
import email as email_lib
import html as html_lib
import imaplib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[assignment]

REALM = "qaguru"
AUTH_URL = os.environ.get("AUTH_URL", "https://auth.qa.guru").rstrip("/")
AUTH_ENV_FILE = Path(os.environ.get("AUTH_ENV", Path.home() / ".config/auth-qa-guru/keycloak.env"))
SMTP_ENV_FILE = Path(os.environ.get("AUTH_SMTP_ENV", Path.home() / ".config/auth-qa-guru/smtp.env"))
PROBE_USER = "smtp-probe"
PROBE_PASSWORD = "SmtpProbe12xx"


def ssl_ctx() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def load_kv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        env[key.strip()] = value
    return env


def auth_env() -> dict[str, str]:
    env = load_kv(AUTH_ENV_FILE)
    env.update({k: v for k, v in load_kv(SMTP_ENV_FILE).items() if v != "" or k not in env})
    missing = [k for k in ("KC_BOOTSTRAP_ADMIN_USERNAME", "KC_BOOTSTRAP_ADMIN_PASSWORD") if not env.get(k)]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)} in {AUTH_ENV_FILE}")
    return env


def merge_smtp_into_keycloak_env(env: dict[str, str]) -> list[str]:
    """Copy KC_SMTP_* from smtp.env into keycloak.env without clobbering other secrets."""
    smtp = load_kv(SMTP_ENV_FILE)
    if not smtp:
        raise SystemExit(f"missing {SMTP_ENV_FILE} — create the mailbox first, never commit it")
    text = AUTH_ENV_FILE.read_text(encoding="utf-8")
    added: list[str] = []
    for key, value in smtp.items():
        env[key] = value
        quoted = f'"{value}"' if any(ch.isspace() for ch in value) else value
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
        if pattern.search(text):
            text = pattern.sub(f"{key}={quoted}", text)
            added.append(f"updated:{key}")
        else:
            if "# SMTP" not in text:
                text = text.rstrip() + "\n\n# SMTP (Beget). Password never in git. ADR 017 §12.\n"
            text += f"{key}={quoted}\n"
            added.append(f"appended:{key}")
    AUTH_ENV_FILE.write_text(text, encoding="utf-8")
    AUTH_ENV_FILE.chmod(0o600)
    return added


def sync_auth_env_to_host() -> None:
    host = os.environ.get("AUTH_SSH", "auth-qa-guru")
    payload = AUTH_ENV_FILE.read_bytes()
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host,
         "sudo install -m 600 -o root -g root /dev/stdin /etc/keycloak/keycloak.env"],
        input=payload,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"failed to sync /etc/keycloak/keycloak.env: {(proc.stderr or b'').decode()[:300]}")
    print("host env: /etc/keycloak/keycloak.env refreshed (root 600)")


def keycloak_token(env: dict[str, str]) -> str:
    form = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": env["KC_BOOTSTRAP_ADMIN_USERNAME"],
            "password": env["KC_BOOTSTRAP_ADMIN_PASSWORD"],
        }
    ).encode()
    req = urllib.request.Request(f"{AUTH_URL}/realms/master/protocol/openid-connect/token", data=form, method="POST")
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
        return json.loads(resp.read())["access_token"]


def kc(method: str, path: str, token: str, body: Any = None) -> Any:
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{AUTH_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_ctx()) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> {exc.code} {exc.read()[:400]!r}") from exc


def smtp_from_env(env: dict[str, str]) -> dict[str, str]:
    host = env.get("KC_SMTP_HOST", "").strip()
    auth = (env.get("KC_SMTP_AUTH") or "false").strip().lower()
    password = env.get("KC_SMTP_PASSWORD", "")
    if not host:
        raise SystemExit("KC_SMTP_HOST empty — fill smtp.env")
    if auth in {"true", "1", "yes"} and not password:
        raise SystemExit("KC_SMTP_AUTH=true but KC_SMTP_PASSWORD is empty")
    return {
        "host": host,
        "port": (env.get("KC_SMTP_PORT") or "465").strip(),
        "from": (env.get("KC_SMTP_FROM") or "").strip(),
        "fromDisplayName": (env.get("KC_SMTP_FROM_DISPLAY") or "QA Guru").strip(),
        "ssl": (env.get("KC_SMTP_SSL") or "true").strip(),
        "starttls": (env.get("KC_SMTP_STARTTLS") or "false").strip(),
        "auth": "true" if auth in {"true", "1", "yes"} else "false",
        "user": env.get("KC_SMTP_USER", "").strip(),
        "password": password,
    }


def public_smtp(smtp: dict[str, str]) -> dict[str, str]:
    out = dict(smtp)
    out["password"] = "***" if smtp.get("password") else ""
    return out


def apply_smtp(env: dict[str, str], smtp: dict[str, str]) -> dict[str, Any]:
    token = keycloak_token(env)
    realm = kc("GET", f"/admin/realms/{REALM}", token)
    if realm.get("loginWithEmailAllowed") is not False:
        raise SystemExit("refusing: loginWithEmailAllowed is on — ADR 017 §5")
    if realm.get("resetPasswordAllowed") is not True:
        raise SystemExit("refusing: resetPasswordAllowed is off")
    realm["smtpServer"] = smtp
    realm["loginWithEmailAllowed"] = False
    realm["registrationEmailAsUsername"] = False
    realm["duplicateEmailsAllowed"] = True
    realm["resetPasswordAllowed"] = True
    kc("PUT", f"/admin/realms/{REALM}", token, realm)
    after = kc("GET", f"/admin/realms/{REALM}", token)
    if after.get("loginWithEmailAllowed") is not False:
        raise SystemExit("smtp applied but loginWithEmailAllowed flipped on")
    return after


def cmd_apply() -> int:
    env = auth_env()
    merged = merge_smtp_into_keycloak_env(env)
    smtp = smtp_from_env(env)
    after = apply_smtp(env, smtp)
    sync_auth_env_to_host()
    print(json.dumps({
        "ok": True,
        "smtp": public_smtp(after.get("smtpServer") or {}),
        "loginWithEmailAllowed": after.get("loginWithEmailAllowed"),
        "resetPasswordAllowed": after.get("resetPasswordAllowed"),
        "env": merged,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_status() -> int:
    env = auth_env()
    token = keycloak_token(env)
    realm = kc("GET", f"/admin/realms/{REALM}", token)
    smtp = realm.get("smtpServer") or {}
    print(json.dumps({
        "ok": True,
        "smtp": public_smtp(smtp) if smtp else {},
        "loginWithEmailAllowed": realm.get("loginWithEmailAllowed"),
        "resetPasswordAllowed": realm.get("resetPasswordAllowed"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_test(to_email: str) -> int:
    """Keycloak testSMTPConnection → one address (the master admin's email for this call)."""
    if not to_email or "@" not in to_email:
        raise SystemExit("--email is required")
    env = auth_env()
    smtp = smtp_from_env(env)
    token = keycloak_token(env)
    admin_name = env["KC_BOOTSTRAP_ADMIN_USERNAME"]
    users = kc("GET", f"/admin/realms/master/users?username={admin_name}&exact=true", token) or []
    if not users:
        raise SystemExit("master admin not found")
    admin = users[0]
    previous = admin.get("email")
    admin["email"] = to_email
    admin["emailVerified"] = True
    kc("PUT", f"/admin/realms/master/users/{admin['id']}", token, admin)
    try:
        kc("POST", f"/admin/realms/{REALM}/testSMTPConnection", token, smtp)
        print(json.dumps({"ok": True, "testSMTPConnection": True, "to": to_email}, ensure_ascii=False))
        return 0
    finally:
        admin["email"] = previous
        kc("PUT", f"/admin/realms/master/users/{admin['id']}", token, admin)


def send_reset(env: dict[str, str], token: str, username: str) -> dict[str, str]:
    if username.lower() in {"*", "all"} or "," in username:
        raise SystemExit("refusing: one username at a time, never the realm")
    users = kc("GET", f"/admin/realms/{REALM}/users?username={urllib.parse.quote(username)}&exact=true", token) or []
    if len(users) != 1:
        raise SystemExit(f"{username!r} matched {len(users)} people — refusing")
    user = users[0]
    email_addr = (user.get("email") or "").strip()
    if not email_addr:
        raise SystemExit(f"{username} has no email")
    redirect = f"{AUTH_URL}/realms/{REALM}/account/"
    path = (
        f"/admin/realms/{REALM}/users/{user['id']}/execute-actions-email"
        f"?client_id=account-console&redirect_uri={urllib.parse.quote(redirect, safe='')}&lifespan=3600"
    )
    kc("PUT", path, token, ["UPDATE_PASSWORD"])
    return {"username": username, "email": email_addr}


def cmd_send_reset(username: str) -> int:
    env = auth_env()
    token = keycloak_token(env)
    result = send_reset(env, token, username.strip())
    print(json.dumps({"ok": True, "reset": result}, ensure_ascii=False, indent=2))
    return 0


def extract_action_link(raw: str) -> str:
    blob = html_lib.unescape(raw)
    match = re.search(r"https://auth\.qa\.guru/[^\"'\s]+action-token[^\"'\s]*", blob, re.I)
    return match.group(0) if match else ""


def imap_clear(env: dict[str, str]) -> None:
    user = env["KC_SMTP_USER"]
    password = env["KC_SMTP_PASSWORD"]
    imap = imaplib.IMAP4_SSL("imap.beget.com", 993)
    try:
        imap.login(user, password)
        imap.select("INBOX")
        typ, data = imap.search(None, "ALL")
        if typ == "OK" and data[0]:
            for uid in data[0].split():
                imap.store(uid, "+FLAGS", "\\Deleted")
            imap.expunge()
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def imap_wait_link(env: dict[str, str], timeout: int = 90) -> str:
    user = env["KC_SMTP_USER"]
    password = env["KC_SMTP_PASSWORD"]
    deadline = time.time() + timeout
    last_err = "no mail"
    while time.time() < deadline:
        imap = imaplib.IMAP4_SSL("imap.beget.com", 993)
        try:
            imap.login(user, password)
            imap.select("INBOX")
            typ, data = imap.search(None, "ALL")
            if typ == "OK" and data[0]:
                ids = data[0].split()
                for uid in reversed(ids[-8:]):
                    typ, msg_data = imap.fetch(uid, "(RFC822)")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    parsed = email_lib.message_from_bytes(raw)
                    payload = ""
                    if parsed.is_multipart():
                        for part in parsed.walk():
                            ctype = part.get_content_type()
                            if ctype in {"text/html", "text/plain"}:
                                payload += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                    else:
                        payload = parsed.get_payload(decode=True).decode(parsed.get_content_charset() or "utf-8", "replace")
                    link = extract_action_link(payload)
                    if link:
                        return link
        except Exception as exc:
            last_err = str(exc)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        time.sleep(4)
    raise SystemExit(f"no Keycloak reset link in IMAP: {last_err}")


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._cur: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._cur = {"action": ad.get("action") or "", "method": (ad.get("method") or "POST").upper(), "fields": {}}
            self.forms.append(self._cur)
        if self._cur is not None and tag in {"input", "button"}:
            name = ad.get("name")
            if name:
                self._cur["fields"][name] = ad.get("value") or ""

    def pick(self) -> dict[str, Any] | None:
        for form in self.forms:
            if "password-new" in form["fields"] or "password" in form["fields"]:
                return form
        return self.forms[0] if self.forms else None


def redact_url(url: str) -> str:
    return re.sub(r"key=[^&]+", "key=REDACTED", url)


def http_session() -> urllib.request.OpenerDirector:
    jar = CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ssl_ctx()),
    )


def submit_new_password(link: str, new_password: str) -> None:
    opener = http_session()
    current = link
    for _ in range(6):
        try:
            with opener.open(current, timeout=30) as resp:
                page = resp.read().decode("utf-8", "replace")
                url = resp.geturl()
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"reset GET {exc.code} {redact_url(getattr(exc, 'url', '') or '')}") from exc
        parser = FormParser()
        parser.feed(page)
        form = parser.pick()
        if form and ("password-new" in form["fields"] or "password" in form["fields"]):
            form["fields"]["password-new"] = new_password
            form["fields"]["password-confirm"] = new_password
            action = form["action"] or url
            if action.startswith("/"):
                action = f"{AUTH_URL}{action}"
            req = urllib.request.Request(
                action,
                data=urllib.parse.urlencode(form["fields"]).encode(),
                method=form["method"],
            )
            with opener.open(req, timeout=30) as resp:
                resp.read()
            return
        if form and form["fields"]:
            action = form["action"] or url
            if action.startswith("/"):
                action = f"{AUTH_URL}{action}"
            req = urllib.request.Request(
                action,
                data=urllib.parse.urlencode(form["fields"]).encode(),
                method=form["method"],
            )
            with opener.open(req, timeout=30) as resp:
                current = resp.geturl()
            continue
        hrefs = re.findall(r'href="([^"]*action-token[^"]*)"', page)
        nxt = None
        for raw in hrefs:
            cand = html_lib.unescape(raw)
            if cand.startswith("/"):
                cand = f"{AUTH_URL}{cand}"
            if "client_id=" in cand or "tab_id=" in cand:
                nxt = cand
                break
        if nxt is None and hrefs:
            cand = html_lib.unescape(hrefs[-1])
            nxt = f"{AUTH_URL}{cand}" if cand.startswith("/") else cand
        if not nxt:
            raise SystemExit(f"reset page has no form at {redact_url(url)}")
        current = nxt
    raise SystemExit(f"reset form never showed password fields at {redact_url(current)}")


def html_login(username: str, password: str) -> bool:
    opener = http_session()
    pkce = "code_challenge_method=S256&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    account = f"{AUTH_URL}/realms/{REALM}/account/"
    start = (
        f"{AUTH_URL}/realms/{REALM}/protocol/openid-connect/auth?client_id=account-console"
        f"&redirect_uri={urllib.parse.quote(account, safe='')}&response_type=code&scope=openid&{pkce}"
    )
    with opener.open(start, timeout=30) as resp:
        page = resp.read().decode("utf-8", "replace")
        url = resp.geturl()
    parser = FormParser()
    parser.feed(page)
    form = parser.pick()
    if not form:
        raise SystemExit("login form missing")
    form["fields"]["username"] = username
    form["fields"]["password"] = password
    action = form["action"] or url
    if action.startswith("/"):
        action = f"{AUTH_URL}{action}"
    req = urllib.request.Request(action, data=urllib.parse.urlencode(form["fields"]).encode(), method="POST")
    with opener.open(req, timeout=30) as resp:
        final = resp.geturl()
        body = resp.read().decode("utf-8", "replace")
    return "/account" in final and "kc-form-login" not in body


def ensure_probe_user(token: str, email_addr: str) -> str:
    existing = kc("GET", f"/admin/realms/{REALM}/users?username={PROBE_USER}&exact=true", token) or []
    payload = {
        "username": PROBE_USER,
        "email": email_addr,
        "enabled": True,
        "emailVerified": True,
        "firstName": "SMTP",
        "lastName": "Probe",
    }
    if existing:
        uid = existing[0]["id"]
        kc("PUT", f"/admin/realms/{REALM}/users/{uid}", token, payload)
    else:
        kc("POST", f"/admin/realms/{REALM}/users", token, payload)
        uid = kc("GET", f"/admin/realms/{REALM}/users?username={PROBE_USER}&exact=true", token)[0]["id"]
    kc("PUT", f"/admin/realms/{REALM}/users/{uid}/reset-password", token, {
        "type": "password",
        "value": "OldProbePass12",
        "temporary": False,
    })
    return uid


def delete_probe_user(token: str, uid: str) -> None:
    kc("DELETE", f"/admin/realms/{REALM}/users/{uid}", token)


def cmd_probe_reset() -> int:
    """One-user reset: mail to the Beget mailbox we can IMAP, change password, delete the probe."""
    env = auth_env()
    token = keycloak_token(env)
    mailbox = env.get("KC_SMTP_USER") or env.get("KC_SMTP_FROM")
    if not mailbox:
        raise SystemExit("KC_SMTP_USER empty")
    uid = ensure_probe_user(token, mailbox)
    try:
        imap_clear(env)
        send_reset(env, token, PROBE_USER)
        link = imap_wait_link(env)
        submit_new_password(link, PROBE_PASSWORD)
        signed_in = html_login(PROBE_USER, PROBE_PASSWORD)
        print(json.dumps({
            "ok": signed_in,
            "probe": PROBE_USER,
            "email": mailbox,
            "signed_in": signed_in,
        }, ensure_ascii=False, indent=2))
        return 0 if signed_in else 1
    finally:
        delete_probe_user(token, uid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apply")
    sub.add_parser("status")
    t = sub.add_parser("test")
    t.add_argument("--email", required=True, help="one mailbox — never a list")
    s = sub.add_parser("send-reset")
    s.add_argument("username")
    sub.add_parser("probe-reset")
    args = parser.parse_args()
    if args.cmd == "apply":
        return cmd_apply()
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "test":
        return cmd_test(args.email)
    if args.cmd == "send-reset":
        return cmd_send_reset(args.username)
    if args.cmd == "probe-reset":
        return cmd_probe_reset()
    raise SystemExit(args.cmd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {redact_url(getattr(exc, 'url', '') or '')}", file=sys.stderr)
        raise
