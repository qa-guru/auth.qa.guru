#!/usr/bin/env python
"""Live Keycloak qaguru vs git vs all *.qa.guru.

Does not print secrets. Does not stop Keycloak. Does not touch LDAP, P9, or boot3.

    python scripts/reconcile-qaguru.py --prod
    python scripts/reconcile-qaguru.py --prod --login-check

Export JSON is gitignored. The markdown matrix is the git artifact.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[assignment]

REPO = Path(__file__).resolve().parents[6]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))
from infra._paths import SELECTEL_API_TOKEN  # noqa: E402

REALM_FILE = kc.DEV_DIR / "realm" / "qaguru-realm.json"
SUMMARY = kc.DEV_DIR / "summaries" / "qaguru-live-matrix.md"
EXPORT_DIR = kc.DEV_DIR / "export"
PLACEHOLDER = re.compile(r"^\$\{[A-Z0-9_]+\}$")
SECRET_KEYS = {"secret", "clientSecret", "value", "password", "client_secret"}
BUILTIN_CLIENTS = {
    "account",
    "account-console",
    "admin-cli",
    "broker",
    "realm-management",
    "security-admin-console",
}
PROJECT_ID = "867a0fed571e4ecdae9dc5a73cd99046"
DNS_API = "https://api.selectel.ru/domains/v2"
ZONE = "qa.guru"
NS = "a.ns.selectel.ru"
PILOTS = ("staff-pilot", "mentor-pilot", "student-pilot")
LIVE_STAFF = "svasenkov"
ATTR_KEEP = (
    "saml.assertion.signature",
    "saml.server.signature",
    "saml.client.signature",
    "saml.force.post.binding",
    "saml.signature.algorithm",
    "saml_name_id_format",
    "saml_force_name_id_format",
    "pkce.code.challenge.method",
    "post.logout.redirect.uris",
)

# Class A/B from ADR 017 + later Atlassian windows. learn is a client, product parked.
SSO_HOSTS: dict[str, dict[str, str]] = {
    "jenkins.qa.guru": {"client": "jenkins", "proto": "oidc", "class": "A", "bg": "escapeHatch admin"},
    "gitlab.qa.guru": {"client": "gitlab", "proto": "oidc", "class": "A", "bg": "local root"},
    "grafana.qa.guru": {"client": "grafana", "proto": "oidc", "class": "A", "bg": "password form admin"},
    "sonar.qa.guru": {"client": "sonar", "proto": "saml", "class": "A", "bg": "local admin"},
    "jira.qa.guru": {"client": "https://jira.qa.guru", "proto": "saml", "class": "A", "bg": "auth_fallback admin"},
    "confluence.qa.guru": {
        "client": "https://confluence.qa.guru",
        "proto": "saml",
        "class": "A",
        "bg": "auth_fallback admin",
    },
    "allure.qa.guru": {"client": "testops", "proto": "oidc", "class": "A", "bg": "/login/system"},
    "selenoid.qa.guru": {"client": "oauth2-proxy", "proto": "oidc-edge", "class": "B", "bg": "Basic htpasswd"},
    "learn.qa.guru": {"client": "learn", "proto": "oidc", "class": "A", "bg": "parked P9, not this window"},
}
PUBLIC = {
    "lab.qa.guru": "course lab, public",
    "demo.qa.guru": "one-page-form mirror, public",
    "book-club.qa.guru": "book club, public",
    "bookclub.qa.guru": "301 → book-club",
    "qa.guru": "apex WP + /u/{handle}",
    "www.qa.guru": "Beget WP 301 → apex",
    "school.qa.guru": "GetCourse, not school IdP",
    "allure-notifications.qa.guru": "GitHub Pages builder",
    "allure.notifications.qa.guru": "301 → allure-notifications",
}
MACHINE = {
    "vault.qa.guru": "Vault token; Keycloak secrets stay off Vault",
    "ollama.qa.guru": "GPU spike, basic auth, not SSO",
    "auth.qa.guru": "IdP itself",
}
TEACHING_AUTH = {
    "auth.niffler.qa.guru": "niffler-auth Spring AS (course), not qaguru",
    "auth.niffler-stage.qa.guru": "niffler-auth stage, not qaguru",
}
SKIP_DNS_TYPES = {"NS", "SOA", "TXT", "MX", "CAA"}
SKIP_PREFIXES = ("_", "hetz_", "passthrough", "reflector")

LOGIN_CHECKS = (
    (
        "jenkins",
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[4]
                / "jenkins-qa-guru-home/jenkins-qa-guru/deploy/oidc.py"
            ),
            "login-check",
        ],
    ),
    (
        "gitlab",
        [
            sys.executable,
            str(Path(__file__).resolve().parents[4] / "gitlab-qa-guru-home/gitlab.qa.guru/deploy/oidc.py"),
            "login-check",
        ],
    ),
    (
        "grafana",
        [
            sys.executable,
            str(Path(__file__).resolve().parents[4] / "grafana-qa-guru-home/grafana.qa.guru/deploy/oidc.py"),
            "login-check",
        ],
    ),
    (
        "sonar",
        [
            sys.executable,
            str(Path(__file__).resolve().parents[4] / "sonar-qa-guru-home/sonar.qa.guru/deploy/saml.py"),
            "login-check",
        ],
    ),
    (
        "jira",
        [
            sys.executable,
            str(Path(__file__).resolve().parents[4] / "jira-qa-guru-home/dev/scripts/saml.py"),
            "login-check",
        ],
    ),
    (
        "confluence",
        [
            sys.executable,
            str(Path(__file__).resolve().parents[4] / "confluence-qa-guru-home/dev/scripts/saml.py"),
            "login-check",
        ],
    ),
    (
        "allure",
        [
            sys.executable,
            str(Path(__file__).resolve().parents[4] / "allure-autotests-cloud-home/dev/scripts/oidc.py"),
            "login-check",
        ],
    ),
    (
        "selenoid",
        [
            sys.executable,
            str(Path(__file__).resolve().parents[4] / "selenoid-qa-guru-home/selenoid-qa-guru/deploy/edge.py"),
            "login-check",
        ],
    ),
)

BG_PROBES = (
    ("jira.qa.guru", "/login.jsp?auth_fallback", ("auth_fallback", "login-page-ui")),
    ("confluence.qa.guru", "/login.action?auth_fallback", ("login-page-ui", "login-link")),
    ("grafana.qa.guru", "/login", ("login",)),
    ("jenkins.qa.guru", "/login", ("j_username", "j_password")),
    ("gitlab.qa.guru", "/users/sign_in", ("user[password]", "password")),
    ("sonar.qa.guru", "/sessions/new", ("SonarQube", "polyfills")),
    ("allure.qa.guru", "/login/system", ("ТестОпс", "__IS_ON_PREMISE__")),
)


def ssl_ctx() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prod", action="store_true", default=True)
    p.add_argument("--stand", action="store_true", help="local :8543 instead of auth.qa.guru")
    p.add_argument("--login-check", action="store_true", help="run existing pilot login-checks (not svasenkov)")
    p.add_argument("--no-dns", action="store_true")
    p.add_argument("--no-http", action="store_true")
    return p.parse_args(argv)


def client_snap(c: dict[str, Any]) -> dict[str, Any]:
    attrs = c.get("attributes") or {}
    kept = {k: attrs[k] for k in ATTR_KEEP if k in attrs}
    mappers = []
    for m in c.get("protocolMappers") or []:
        cfg = {k: v for k, v in sorted((m.get("config") or {}).items())}
        mappers.append(
            {
                "name": m.get("name"),
                "protocolMapper": m.get("protocolMapper"),
                "config": cfg,
            }
        )
    mappers.sort(key=lambda x: (x["name"] or "", x["protocolMapper"] or ""))
    return {
        "clientId": c.get("clientId"),
        "name": c.get("name") or "",
        "protocol": c.get("protocol"),
        "enabled": c.get("enabled", True),
        "publicClient": c.get("publicClient"),
        "standardFlowEnabled": c.get("standardFlowEnabled"),
        "serviceAccountsEnabled": c.get("serviceAccountsEnabled"),
        "directAccessGrantsEnabled": c.get("directAccessGrantsEnabled"),
        "redirectUris": sorted(c.get("redirectUris") or []),
        "webOrigins": sorted(c.get("webOrigins") or []),
        "attributes": kept,
        "mappers": mappers,
    }


def tracked_clients(realm: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in realm.get("clients") or []:
        cid = c.get("clientId") or ""
        if not cid or cid in BUILTIN_CLIENTS:
            continue
        out[cid] = client_snap(c)
    return out


def group_paths(realm: dict[str, Any]) -> set[str]:
    paths: set[str] = set()

    def walk(nodes: list[Any]) -> None:
        for g in nodes or []:
            path = g.get("path") or (f"/{g['name']}" if g.get("name") else "")
            if path:
                paths.add(path)
            walk(g.get("subGroups") or [])

    walk(realm.get("groups") or [])
    return paths


def idp_snap(realm: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i in realm.get("identityProviders") or []:
        cfg = {k: v for k, v in (i.get("config") or {}).items() if k not in SECRET_KEYS}
        rows.append(
            {
                "alias": i.get("alias"),
                "enabled": i.get("enabled"),
                "providerId": i.get("providerId"),
                "config_keys": sorted((i.get("config") or {}).keys()),
                "clientId_set": bool((i.get("config") or {}).get("clientId")),
                "non_secret": cfg,
            }
        )
    return rows


def is_noise(item: dict[str, Any]) -> bool:
    path = item["path"]
    git_v, live_v = item.get("git"), item.get("live")
    if path.endswith(".defaultClientScopes"):
        return True
    if git_v is None and path.split(".")[-1] in {
        "standardFlowEnabled",
        "serviceAccountsEnabled",
        "directAccessGrantsEnabled",
        "publicClient",
    }:
        return True
    if path.endswith(".webOrigins") and git_v == []:
        return True
    if live_v == "+" or live_v == '"+"':
        return True
    if path == "identityProviders.github.enabled":
        return True
    return False


def diff_dict(git_v: Any, live_v: Any, path: str, out: list[dict[str, Any]]) -> None:
    if git_v == live_v:
        return
    if isinstance(git_v, dict) and isinstance(live_v, dict):
        keys = sorted(set(git_v) | set(live_v))
        for k in keys:
            if k in git_v and k in live_v:
                diff_dict(git_v[k], live_v[k], f"{path}.{k}", out)
            elif k in git_v:
                out.append({"path": f"{path}.{k}", "git": git_v[k], "live": None})
            else:
                out.append({"path": f"{path}.{k}", "git": None, "live": live_v[k]})
        return
    if isinstance(git_v, list) and isinstance(live_v, list):
        if git_v != live_v:
            out.append({"path": path, "git": git_v, "live": live_v})
        return
    out.append({"path": path, "git": git_v, "live": live_v})


def realm_flags(realm: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "loginWithEmailAllowed",
        "registrationEmailAsUsername",
        "duplicateEmailsAllowed",
        "editUsernameAllowed",
        "resetPasswordAllowed",
        "webAuthnPolicyPasswordlessPasskeysEnabled",
        "webAuthnPolicyPasswordlessRpId",
        "webAuthnPolicyPasswordlessUserVerificationRequirement",
    )
    return {k: realm.get(k) for k in keys}


def project_token() -> str:
    token = SELECTEL_API_TOKEN.read_text().strip()
    body = json.dumps({"token": {"project_id": PROJECT_ID}}).encode()
    req = urllib.request.Request(
        "https://api.selectel.ru/vpc/resell/v2/tokens",
        data=body,
        headers={"X-Token": token, "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
        data = json.loads(resp.read())
    return data["token"]["id"]


def list_qa_guru_rrsets() -> list[dict[str, Any]]:
    tok = project_token()
    headers = {"X-Auth-Token": tok, "Accept": "application/json"}
    req = urllib.request.Request(f"{DNS_API}/zones?filter={ZONE}", headers=headers)
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
        zones = json.loads(resp.read())
    zone_id = None
    for z in zones.get("result") or []:
        if z.get("name") in (ZONE, f"{ZONE}."):
            zone_id = z["id"]
            break
    if not zone_id:
        raise SystemExit("Selectel zone qa.guru not found")
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        req = urllib.request.Request(
            f"{DNS_API}/zones/{zone_id}/rrset?limit=1000&offset={offset}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
            data = json.loads(resp.read())
        out.extend(data.get("result") or [])
        nxt = data.get("next_offset")
        if not nxt:
            break
        offset = int(nxt)
    return out


def host_from_rrset(name: str) -> str:
    n = name.rstrip(".")
    if n == ZONE:
        return ZONE
    return n


def classify_host(host: str) -> tuple[str, str]:
    if host in SSO_HOSTS:
        row = SSO_HOSTS[host]
        return f"SSO {row['class']} {row['proto']}", row["bg"]
    if host in PUBLIC:
        return "public", PUBLIC[host]
    if host in MACHINE:
        return "machine/idp", MACHINE[host]
    if host in TEACHING_AUTH:
        return "teaching-auth", TEACHING_AUTH[host]
    if host.endswith(".cb.qa.guru") or host == "dozzle.cb.qa.guru":
        return "zoo", "cb_* compose, not school SSO"
    if "niffler" in host:
        return "zoo", "niffler, own auth on auth.niffler.*"
    if "realworld" in host:
        return "zoo", "realworld teaching app"
    if "minecraft" in host:
        return "zoo", "minecraft"
    if ".testing.qa.guru" in host or host.endswith("testing.qa.guru"):
        return "stale", "old K8s testing"
    for stale in ("argocd", "harbor", "rancher", "hubble", "wp.qa.guru", "grafana.testing"):
        if host.startswith(stale) or host == stale or host == f"{stale}.qa.guru":
            return "stale", "dismantled K8s / leftover"
    if host in ("autoconfig.qa.guru", "autodiscover.qa.guru"):
        return "mail", "mail autoconfig"
    if host.startswith("www1."):
        return "external", "www1 leftover — probe"
    if host in ("provisioning.qa.guru", "lms.qa.guru"):
        return "lms", "LMS/registry; P9 not this window"
    return "other", ""


def http_probe(host: str) -> dict[str, Any]:
    url = f"https://{host}/"
    result: dict[str, Any] = {"host": host, "url": url, "status": None, "final": None, "idp": False, "error": ""}
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "qaguru-reconcile/1"})
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl_ctx()),
            urllib.request.HTTPRedirectHandler(),
        )
        with opener.open(req, timeout=20) as resp:
            result["status"] = resp.status
            result["final"] = resp.geturl()
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["final"] = exc.geturl() if hasattr(exc, "geturl") else url
        loc = exc.headers.get("Location") if exc.headers else None
        if loc:
            result["final"] = urllib.parse.urljoin(url, loc)
    except Exception as exc:  # noqa: BLE001 — probe must not abort the matrix
        result["error"] = type(exc).__name__
    final = result.get("final") or ""
    result["idp"] = "auth.qa.guru" in final
    return result


def bg_probe(host: str, path: str, needles: tuple[str, ...]) -> dict[str, Any]:
    url = f"https://{host}{path}"
    row: dict[str, Any] = {"host": host, "path": path, "status": None, "ok": False, "error": ""}
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "qaguru-reconcile/1"})
        with urllib.request.urlopen(req, timeout=20, context=ssl_ctx()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            row["status"] = resp.status
            row["final"] = resp.geturl()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        row["status"] = exc.code
        row["final"] = url
    except Exception as exc:  # noqa: BLE001
        row["error"] = type(exc).__name__
        return row
    low = body.lower()
    row["ok"] = any(n.lower() in low for n in needles)
    row["has_auth_host"] = "auth.qa.guru" in body or "auth.qa.guru" in (row.get("final") or "")
    if "auth.qa.guru" in (row.get("final") or ""):
        row["ok"] = False
    return row


def selenoid_bg() -> dict[str, Any]:
    """Break-glass on selenoid is Basic, not a form. Check /wd/hub still challenges."""
    url = "https://selenoid.qa.guru/wd/hub/status"
    row: dict[str, Any] = {"host": "selenoid.qa.guru", "path": "/wd/hub/status", "ok": False}
    try:
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=15, context=ssl_ctx())
        row["status"] = 200
        row["ok"] = True
        row["note"] = "hub answered without Basic — unexpected"
    except urllib.error.HTTPError as exc:
        row["status"] = exc.code
        www = exc.headers.get("WWW-Authenticate") or ""
        row["ok"] = exc.code in (401, 403) and "basic" in www.lower()
        row["www_authenticate"] = "Basic" if "basic" in www.lower() else "set" if www else "missing"
        row["note"] = "machine /wd/hub still challenges Basic (not IdP)"
    except Exception as exc:  # noqa: BLE001
        row["error"] = type(exc).__name__
    ui = http_probe("selenoid.qa.guru")
    row["ui_to_idp"] = bool(ui.get("idp") or (ui.get("status") in (302, 303, 307, 308)))
    row["ui_status"] = ui.get("status")
    row["ui_final"] = ui.get("final")
    return row


def redact_for_file(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in SECRET_KEYS and isinstance(v, str) and not PLACEHOLDER.match(v):
                out[k] = "***"
            else:
                out[k] = redact_for_file(v)
        return out
    if isinstance(node, list):
        return [redact_for_file(x) for x in node]
    return node


def run_login_check(name: str, cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if LIVE_STAFF in text.lower() and "not used" not in text.lower() and "acceptance" not in text.lower():
        # Jenkins rollback snippet mentions svasenkov; login-check must not be that command.
        pass
    pilots_seen = [p for p in PILOTS if p in text]
    return {
        "service": name,
        "exit": proc.returncode,
        "ok": proc.returncode == 0,
        "pilots_mentioned": pilots_seen,
        "svasenkov_in_output": LIVE_STAFF in text,
        "lines": len(text.splitlines()),
    }


def write_md(report: dict[str, Any]) -> None:
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    drift = report["drift"]
    lines: list[str] = [
        "# Живая матрица Keycloak `qaguru` × `*.qa.guru`",
        "",
        f"**Снято:** {report['when']} · realm [auth.qa.guru](https://auth.qa.guru) · пилоты `{', '.join(PILOTS)}`, не `{LIVE_STAFF}`.",
        "",
        "Не LDAP. Не P9 (клиент `learn` есть, продукт не спавнили). Секреты не в git и не в этом файле.",
        "",
        "Источник правды realm-as-code — [`../realm/qaguru-realm.json`](../realm/qaguru-realm.json). Live export — gitignored `dev/export/`.",
        "",
        "## Realm live vs git",
        "",
        f"- Клиенты (без builtin): git **{len(report['git_clients'])}**, live **{len(report['live_clients'])}**.",
        f"- Только git: `{', '.join(report['only_git']) or '—'}`",
        f"- Только live: `{', '.join(report['only_live']) or '—'}`",
        f"- Группы git: `{', '.join(report['groups_git'])}`",
        f"- Группы live extra: `{', '.join(report['groups_live_extra']) or '—'}`",
        f"- Группы missing live: `{', '.join(report['groups_missing_live']) or '—'}`",
        f"- GitHub IdP git.enabled=`{report['github_git']}` live.enabled=`{report['github_live']}` — в файле **false** + `${{ENV}}` намеренно (кнопка без credentials).",
        f"- Людей в realm: **{report['people_count']}**. Пилоты: {', '.join(f'`{k}` {v}' for k, v in report['pilots'].items())}. `{LIVE_STAFF}`: {report['svasenkov']} (живой staff, не приёмка).",
        "",
        "### Осмысленный drift (не UUID / не masked secret / не default flow)",
        "",
    ]
    if not drift:
        lines.append("Расхождений по tracked-полям нет.")
    else:
        lines += [
            "| Путь | git | live |",
            "|------|-----|------|",
        ]
        for d in drift:
            g = json.dumps(d["git"], ensure_ascii=False) if d["git"] is not None else "—"
            lv = json.dumps(d["live"], ensure_ascii=False) if d["live"] is not None else "—"
            if len(g) > 120:
                g = g[:117] + "…"
            if len(lv) > 120:
                lv = lv[:117] + "…"
            lines.append(f"| `{d['path']}` | {g} | {lv} |")
    lines += [
        "",
        "## `*.qa.guru` (GoDaddy DNS A/AAAA/CNAME)",
        "",
        "| Host | DNS | HTTP | Класс | IdP / break-glass |",
        "|------|-----|------|-------|-------------------|",
    ]
    for row in report["hosts"]:
        http = row.get("http") or {}
        st = http.get("status") if http else "—"
        err = http.get("error") or ""
        http_s = f"{st}" + (f" {err}" if err else "")
        idp = "→ IdP" if http.get("idp") else ""
        lines.append(
            f"| [{row['host']}](https://{row['host']}/) | {row['dns']} | {http_s} {idp} | {row['klass']} | {row['note']} |"
        )
    lines += [
        "",
        "## Break-glass (IdP **не** гасили)",
        "",
        "Форма/вызов локального входа при живом Keycloak. Полный `docker stop` IdP — в окнах P2b–P5 / Jira / Confluence, здесь не повторяли: активный чат, общий IdP.",
        "",
        "| Сервис | Проба | HTTP | ok |",
        "|--------|-------|------|----|",
    ]
    for b in report["break_glass"]:
        lines.append(
            f"| {b.get('host')} | `{b.get('path', '')}` | {b.get('status')} | {'yes' if b.get('ok') else 'no'} |"
        )
    if report.get("login_checks"):
        lines += [
            "",
            "## Login-check пилотов (не svasenkov)",
            "",
            "| Сервис | exit | пилоты в выводе | заметка |",
            "|--------|------|-----------------|--------|",
        ]
        for lc in report["login_checks"]:
            note = lc.get("note") or ""
            lines.append(
                f"| {lc['service']} | {'0' if lc.get('ok') else lc.get('exit', 1)} | {', '.join(lc.get('pilots_mentioned') or []) or '—'} | {note} |"
            )
    lines += [
        "",
        "## Вне этого окна",
        "",
        "- LDAP не поднимали.",
        "- P9 / cut GC не спавнили; клиент `learn` сверён как запись realm, не как продукт.",
        "",
        f"Пересъём: `python projects/services-home/auth-qa-guru-home/auth.qa.guru/dev/scripts/reconcile-qaguru.py --prod`",
        "",
    ]
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    prod = not ns.stand
    env = kc.load_env(kc.PROD_ENV if prod else None)
    token = kc.admin_token(env)
    git_realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    live_realm = kc.post(
        env,
        token,
        f"/admin/realms/{kc.REALM}/partial-export?exportGroupsAndRoles=true&exportClients=true",
        {},
    )
    EXPORT_DIR.mkdir(exist_ok=True)
    dump = EXPORT_DIR / ("qaguru-realm-prod.json" if prod else "qaguru-realm.json")
    dump.write_text(json.dumps(redact_for_file(live_realm), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    git_c = tracked_clients(git_realm)
    live_c = tracked_clients(live_realm)
    only_git = sorted(set(git_c) - set(live_c))
    only_live = sorted(set(live_c) - set(git_c))
    drift: list[dict[str, Any]] = []
    for cid in sorted(set(git_c) & set(live_c)):
        diff_dict(git_c[cid], live_c[cid], cid, drift)
    git_g = group_paths(git_realm)
    live_g = group_paths(live_realm)

    git_idp = {i["alias"]: i for i in idp_snap(git_realm)}
    live_idp = {i["alias"]: i for i in idp_snap(live_realm)}
    github_git = (git_idp.get("github") or {}).get("enabled")
    github_live = (live_idp.get("github") or {}).get("enabled")
    if github_git != github_live:
        drift.append(
            {
                "path": "identityProviders.github.enabled",
                "git": github_git,
                "live": github_live,
            }
        )

    flag_drift: list[dict[str, Any]] = []
    diff_dict(realm_flags(git_realm), realm_flags(live_realm), "realm", flag_drift)
    # RP ID in git is placeholder; live is qa.guru — expected.
    flag_drift = [d for d in flag_drift if "webAuthnPolicyPasswordlessRpId" not in d["path"]]
    drift.extend(flag_drift)
    drift = [d for d in drift if not is_noise(d)]

    users = kc.get(env, token, f"/admin/realms/{kc.REALM}/users?briefRepresentation=true&max=1000") or []
    names = {u.get("username") for u in users}
    people = names - {
        "svc-provisioning",
        "service-account-svc-provisioning",
        "service-account-svc-selenoid-token",
    }
    pilots = {p: p in names for p in PILOTS}

    hosts: list[dict[str, Any]] = []
    if not ns.no_dns:
        rrsets = list_qa_guru_rrsets()
        seen: set[str] = set()
        dns_rows: list[dict[str, Any]] = []
        for rr in rrsets:
            rtype = rr.get("type") or ""
            if rtype in SKIP_DNS_TYPES:
                continue
            host = host_from_rrset(rr.get("name") or "")
            if any(host.startswith(p) or f".{p}" in f".{host}" and host.startswith("_") for p in ("_",)):
                if host.startswith("_"):
                    continue
            if host.startswith("_"):
                continue
            recs = rr.get("records") or []
            contents = []
            for rec in recs:
                content = rec.get("content") if isinstance(rec, dict) else rec
                if content:
                    contents.append(str(content).rstrip("."))
            dns_rows.append({"host": host, "type": rtype, "content": ", ".join(contents)})
            seen.add(host)
        # Always include SSO hosts even if CNAME-only skipped
        for h in list(SSO_HOSTS) + list(PUBLIC) + list(MACHINE):
            if h not in seen:
                dns_rows.append({"host": h, "type": "?", "content": "not in Selectel A/CNAME dump"})
        dns_rows.sort(key=lambda r: r["host"])
        probes: dict[str, dict[str, Any]] = {}
        if not ns.no_http:
            with ThreadPoolExecutor(max_workers=12) as pool:
                futs = {pool.submit(http_probe, r["host"]): r["host"] for r in dns_rows}
                for fut in as_completed(futs):
                    probes[futs[fut]] = fut.result()
        for r in dns_rows:
            klass, note = classify_host(r["host"])
            extra = SSO_HOSTS.get(r["host"], {})
            if extra:
                note = f"client `{extra['client']}` · {extra['bg']}"
            hosts.append(
                {
                    "host": r["host"],
                    "dns": f"{r['type']} {r['content']}",
                    "http": probes.get(r["host"]),
                    "klass": klass,
                    "note": note,
                }
            )

    break_glass = []
    if not ns.no_http:
        for host, path, needles in BG_PROBES:
            break_glass.append(bg_probe(host, path, needles))
        break_glass.append(selenoid_bg())

    login_checks = []
    if ns.login_check:
        for name, cmd in LOGIN_CHECKS:
            login_checks.append(run_login_check(name, cmd))
    else:
        sidecar = EXPORT_DIR / "login-check-summary.json"
        if sidecar.is_file():
            login_checks = json.loads(sidecar.read_text(encoding="utf-8"))

    report = {
        "ok": not only_git and all(pilots.values()) and (not login_checks or all(x["ok"] for x in login_checks)),
        "when": now_iso(),
        "base_url": kc.base_url(env),
        "git_clients": sorted(git_c),
        "live_clients": sorted(live_c),
        "only_git": only_git,
        "only_live": only_live,
        "groups_git": sorted(git_g),
        "groups_live_extra": sorted(live_g - git_g),
        "groups_missing_live": sorted(git_g - live_g),
        "github_git": github_git,
        "github_live": github_live,
        "people_count": len(people),
        "pilots": {k: "yes" if v else "MISSING" for k, v in pilots.items()},
        "svasenkov": "present-live-staff" if LIVE_STAFF in names else "MISSING",
        "drift": drift,
        "hosts": hosts,
        "break_glass": break_glass,
        "login_checks": login_checks,
        "export": str(dump),
        "note": "secrets redacted; github.enabled live=true vs git=false is intentional",
    }
    EXPORT_DIR.joinpath("qaguru-reconcile.json").write_text(
        json.dumps(redact_for_file(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_md(report)
    public = {
        "ok": report["ok"],
        "when": report["when"],
        "clients_git": report["git_clients"],
        "clients_live": report["live_clients"],
        "only_git": only_git,
        "only_live": only_live,
        "drift_paths": [d["path"] for d in drift],
        "people_count": report["people_count"],
        "pilots": report["pilots"],
        "svasenkov": report["svasenkov"],
        "hosts": len(hosts),
        "break_glass_ok": [b.get("host") for b in break_glass if b.get("ok")],
        "break_glass_fail": [b.get("host") for b in break_glass if not b.get("ok")],
        "login_checks": [{"service": x["service"], "ok": x["ok"]} for x in login_checks],
        "matrix": str(SUMMARY),
        "export": str(dump),
    }
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        print(json.dumps({"ok": False, "http": exc.code, "url": exc.url}), file=sys.stderr)
        raise SystemExit(2)
