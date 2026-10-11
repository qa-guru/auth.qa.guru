#!/usr/bin/env python
"""P9 smoke resource server for learn.qa.guru — OIDC BFF + local break-glass.

Not the LMS product (qa-guru-backend / qa-guru-frontend). Cookie session, PKCE,
no access token in the page. Groups /staff /mentors /students.

    python scripts/learn-stub.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

CLIENT_ID = "learn"
SESSION_COOKIE = "learn_session"
PENDING: dict[str, dict[str, str]] = {}


def load() -> dict[str, str]:
    return kc.load_env()


def issuer(env: dict[str, str]) -> str:
    return f"{kc.base_url(env)}/realms/{kc.REALM}"


def session_secret(env: dict[str, str]) -> bytes:
    raw = (env.get("LEARN_SESSION_SECRET") or "").strip()
    if not raw:
        raise SystemExit("LEARN_SESSION_SECRET missing in dev/.env — python scripts/learn.py seed-env")
    return raw.encode()


def sign(env: dict[str, str], payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(session_secret(env), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def unsign(env: dict[str, str], token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    expect = hmac.new(session_secret(env), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (json.JSONDecodeError, ValueError):
        return None


def cookies_of(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    raw = handler.headers.get("Cookie") or ""
    out: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" in part:
            key, _, value = part.strip().partition("=")
            out[key] = value
    return out


def groups_of(claim: Any) -> list[str]:
    if not claim:
        return []
    if isinstance(claim, str):
        return [claim]
    if isinstance(claim, list):
        return [str(g) for g in claim]
    return []


def role_of(groups: list[str]) -> str | None:
    if "/staff" in groups:
        return "staff"
    if "/mentors" in groups:
        return "mentor"
    if "/students" in groups or "/contour" in groups:
        return "student"
    return None


def jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    pad = "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def http_json(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode("utf-8", "replace")[:200]
        return exc.code, body
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


def page(title: str, body: str, *, status_attr: str = "") -> bytes:
    extra = f" data-status=\"{escape(status_attr)}\"" if status_attr else ""
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body {{ font: 16px/1.45 system-ui, sans-serif; margin: 2rem auto; max-width: 40rem; color: #1b1b1b; }}
    a.btn, button {{ display: inline-block; padding: .5rem .9rem; margin: .25rem .25rem 0 0;
      background: #1f4b99; color: #fff; text-decoration: none; border: 0; border-radius: 6px; cursor: pointer; }}
    form.local {{ margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid #ddd; }}
    label {{ display: block; margin: .4rem 0 .15rem; }}
    input {{ padding: .35rem .5rem; width: 16rem; }}
    .panel {{ padding: 1rem; border: 1px solid #c9d4e8; border-radius: 8px; background: #f4f7fb; }}
    .muted {{ color: #555; font-size: .9rem; }}
  </style>
</head>
<body{extra}>
{body}
</body>
</html>
"""
    return html.encode()


def landing(session: dict[str, Any] | None) -> bytes:
    if session:
        return app_page(session)
    body = """
  <h1>learn.qa.guru</h1>
  <p class="muted">Стык с IdP (окно P9). Не LMS: курсов здесь нет, только вход и роль из группы Keycloak.</p>
  <p><a class="btn" id="login-oidc" href="/login">Войти через Keycloak</a></p>
  <form class="local" id="login-system" method="post" action="/login/system">
    <p>Локальный админ — жив, когда IdP выключен.</p>
    <label for="username">username</label>
    <input id="username" name="username" autocomplete="username">
    <label for="password">password</label>
    <input id="password" name="password" type="password" autocomplete="current-password">
    <p><button type="submit">Войти локально</button></p>
  </form>
"""
    return page("learn — вход", body)


def app_page(session: dict[str, Any]) -> bytes:
    role = session.get("role") or ""
    user = session.get("username") or ""
    auth = session.get("auth") or ""
    groups = ", ".join(session.get("groups") or [])
    views = {
        "staff": "<div class=\"panel\" id=\"view-staff\"><h2>Кабинет сотрудника</h2><p>Потоки, выдача, доступ менторов.</p></div>",
        "mentor": "<div class=\"panel\" id=\"view-mentor\"><h2>Проверка ДЗ</h2><p>Очередь работ потока. Без админки школы.</p></div>",
        "student": "<div class=\"panel\" id=\"view-student\"><h2>Мои курсы</h2><p>Учёба. Чужих потоков и админки нет.</p></div>",
    }
    view = views.get(role, "<p>Нет роли для learn.</p>")
    body = f"""
  <h1>learn.qa.guru</h1>
  <p id="who" data-username="{escape(user)}" data-role="{escape(role)}" data-auth="{escape(auth)}">
    {escape(user)} · {escape(role)} · {escape(auth)}
  </p>
  <p class="muted">группы: {escape(groups) or "—"}</p>
  {view}
  <p><a href="/logout">Выйти</a></p>
"""
    return page(f"learn — {role}", body, status_attr="in")


def forbidden(user: str, groups: list[str]) -> bytes:
    body = f"""
  <h1>Нет роли</h1>
  <p id="who" data-username="{escape(user)}" data-role="" data-auth="oidc">
    {escape(user)} есть в Keycloak, но не в /staff, /mentors или /students.
  </p>
  <p class="muted">группы: {escape(", ".join(groups)) or "—"}</p>
  <p><a href="/logout">Выйти</a></p>
"""
    return page("learn — 403", body, status_attr="forbidden")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def env(self) -> dict[str, str]:
        return load()

    def origin(self) -> str:
        host = self.headers.get("Host") or f"127.0.0.1:{os.environ.get('LEARN_STUB_PORT', '3040')}"
        return f"http://{host}"

    def session(self) -> dict[str, Any] | None:
        return unsign(self.env(), cookies_of(self).get(SESSION_COOKIE))

    def send_bytes(self, status: int, body: bytes, *, content_type: str, extra: list[str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for header in extra or []:
            name, _, value = header.partition(":")
            self.send_header(name.strip(), value.strip())
        self.end_headers()
        self.wfile.write(body)

    def html(self, status: int, body: bytes, extra: list[str] | None = None) -> None:
        self.send_bytes(status, body, content_type="text/html; charset=utf-8", extra=extra)

    def json(self, status: int, payload: dict[str, Any], extra: list[str] | None = None) -> None:
        self.send_bytes(
            status,
            json.dumps(payload, ensure_ascii=False).encode(),
            content_type="application/json; charset=utf-8",
            extra=extra,
        )

    def redirect(self, location: str, extra: list[str] | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        for header in extra or []:
            name, _, value = header.partition(":")
            self.send_header(name.strip(), value.strip())
        self.send_header("Content-Length", "0")
        self.end_headers()

    def set_session(self, payload: dict[str, Any]) -> str:
        return f"Set-Cookie: {SESSION_COOKIE}={sign(self.env(), payload)}; Path=/; HttpOnly; SameSite=Lax"

    def clear_session(self) -> list[str]:
        return [
            f"Set-Cookie: {SESSION_COOKIE}=; Path=/; HttpOnly; Max-Age=0",
        ]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self.json(200, {"ok": True, "service": "learn-stub"})
            return
        if path == "/":
            self.html(200, landing(self.session()))
            return
        if path == "/me":
            sess = self.session()
            if not sess:
                self.json(401, {"ok": False, "error": "unauthorized"})
                return
            self.json(200, {"ok": True, **sess})
            return
        if path == "/app":
            sess = self.session()
            if not sess:
                self.redirect("/")
                return
            self.html(200, app_page(sess))
            return
        if path == "/login":
            self.start_oidc()
            return
        if path == "/oauth2/callback":
            self.finish_oidc(urllib.parse.parse_qs(parsed.query))
            return
        if path == "/login/system":
            self.html(200, landing(self.session()))
            return
        if path == "/logout":
            self.redirect("/", extra=self.clear_session())
            return
        self.html(404, page("404", "<h1>Нет такой страницы</h1><p><a href=\"/\">На вход</a></p>"))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace")).items()}
        if parsed.path == "/login/system":
            self.local_login(form)
            return
        self.html(404, page("404", "<h1>Нет такой страницы</h1>"))

    def start_oidc(self) -> None:
        env = self.env()
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        redirect_uri = f"{self.origin()}/oauth2/callback"
        PENDING[state] = {"verifier": verifier, "redirect_uri": redirect_uri}
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{issuer(env)}/protocol/openid-connect/auth?{urllib.parse.urlencode(params)}"
        self.redirect(url)

    def finish_oidc(self, query: dict[str, list[str]]) -> None:
        env = self.env()
        error = (query.get("error") or [""])[0]
        if error:
            self.html(401, page("OIDC error", f"<h1>Keycloak отказал</h1><p>{escape(error)}</p><p><a href=\"/\">Назад</a></p>"))
            return
        state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        pending = PENDING.pop(state, None)
        if not pending or not code:
            self.html(400, page("OIDC", "<h1>Нет code/state</h1><p><a href=\"/login\">Ещё раз</a></p>"))
            return
        secret = (env.get("KC_CLIENT_SECRET_LEARN") or "").strip()
        form = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": secret,
                "code": code,
                "redirect_uri": pending["redirect_uri"],
                "code_verifier": pending["verifier"],
            }
        ).encode()
        status, token = http_json(
            f"{issuer(env)}/protocol/openid-connect/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if status != 200 or not isinstance(token, dict):
            self.html(401, page("OIDC", "<h1>Не обменяли code</h1><p><a href=\"/login\">Ещё раз</a></p>"))
            return
        access = token.get("access_token") or ""
        u_status, userinfo = http_json(
            f"{issuer(env)}/protocol/openid-connect/userinfo",
            headers={"Authorization": f"Bearer {access}"},
        )
        claims = userinfo if u_status == 200 and isinstance(userinfo, dict) else jwt_payload(token.get("id_token") or access)
        username = str(claims.get("preferred_username") or claims.get("sub") or "")
        groups = groups_of(claims.get("groups"))
        if not groups:
            groups = groups_of(jwt_payload(token.get("id_token") or "").get("groups"))
        role = role_of(groups)
        if not username or not role:
            self.html(403, forbidden(username, groups))
            return
        payload = {"username": username, "role": role, "groups": groups, "auth": "oidc"}
        self.redirect("/app", extra=[self.set_session(payload)])

    def local_login(self, form: dict[str, str]) -> None:
        env = self.env()
        user = (form.get("username") or "").strip()
        password = form.get("password") or ""
        expect_user = (env.get("LEARN_BREAKGLASS_USERNAME") or "admin").strip()
        expect_pass = (env.get("LEARN_BREAKGLASS_PASSWORD") or "").strip()
        if not expect_pass or user != expect_user or password != expect_pass:
            body = landing(None).replace(
                "<h1>learn.qa.guru</h1>",
                "<h1>learn.qa.guru</h1><p id=\"login-error\">Неверный локальный пароль</p>",
                1,
            )
            self.html(401, body)
            return
        payload = {
            "username": expect_user,
            "role": "staff",
            "groups": ["/staff"],
            "auth": "local",
        }
        self.redirect("/app", extra=[self.set_session(payload)])


def main() -> None:
    env = load()
    port = int(env.get("LEARN_STUB_PORT") or os.environ.get("LEARN_STUB_PORT") or "3040")
    session_secret(env)
    if not (env.get("KC_CLIENT_SECRET_LEARN") or "").strip():
        raise SystemExit("KC_CLIENT_SECRET_LEARN missing — python scripts/learn.py seed-env")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"learn-stub http://127.0.0.1:{port}/health", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
