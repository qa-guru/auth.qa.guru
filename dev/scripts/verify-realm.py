#!/usr/bin/env python
"""Acceptance for the P1 stand: assert the running realm matches what ADR 017 froze.

Every check maps to a decision, not to a Keycloak default — the point is to catch a realm that
drifted away from the ADR, and to catch a secret that leaked into the tracked realm file.

    python scripts/verify-realm.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

REALM_FILE = kc.DEV_DIR / "realm" / "qaguru-realm.json"
PLACEHOLDER = re.compile(r"^\$\{[A-Z0-9_]+\}$")
SECRET_KEYS = {"secret", "clientSecret", "value", "password"}

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, bool(ok), detail))


def secrets_in_realm_file() -> list[str]:
    """Every secret-ish field in the tracked realm file must be a ${ENV} placeholder."""
    leaks: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in SECRET_KEYS and isinstance(value, str) and not PLACEHOLDER.match(value):
                    leaks.append(f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(json.loads(REALM_FILE.read_text(encoding="utf-8")), "realm")
    return leaks


def main() -> None:
    env = kc.load_env()
    token = kc.admin_token(env)
    realm = kc.get(env, token, f"/admin/realms/{kc.REALM}")

    # Ключ человека — username = handle. Email вторичен: 183 коллизии в data room (ADR 017 п. 5).
    check("email is not a login", realm["loginWithEmailAllowed"] is False)
    check("email is not a username", realm["registrationEmailAsUsername"] is False)
    check("duplicate emails allowed", realm["duplicateEmailsAllowed"] is True)
    check("users cannot rename themselves", realm["editUsernameAllowed"] is False)
    check("login theme is qaguru", realm.get("loginTheme") == "qaguru", str(realm.get("loginTheme")))
    check("SSO idle is 2 days", realm.get("ssoSessionIdleTimeout") == 172800, str(realm.get("ssoSessionIdleTimeout")))
    check("SSO max is 7 days", realm.get("ssoSessionMaxLifespan") == 604800, str(realm.get("ssoSessionMaxLifespan")))
    check(
        "remember-me idle is 7 days",
        realm.get("ssoSessionIdleTimeoutRememberMe") == 604800,
        str(realm.get("ssoSessionIdleTimeoutRememberMe")),
    )
    check(
        "remember-me max is 14 days",
        realm.get("ssoSessionMaxLifespanRememberMe") == 1209600,
        str(realm.get("ssoSessionMaxLifespanRememberMe")),
    )

    # Passkey + обязательный fallback (ADR 017 п. 8).
    check("passkeys enabled", realm.get("webAuthnPolicyPasswordlessPasskeysEnabled") is True)
    check(
        "webauthn RP ID from env",
        realm.get("webAuthnPolicyPasswordlessRpId") == env.get("KC_WEBAUTHN_RP_ID"),
        realm.get("webAuthnPolicyPasswordlessRpId", ""),
    )
    check("user verification required", realm.get("webAuthnPolicyPasswordlessUserVerificationRequirement") == "required")
    check("password fallback kept", realm["resetPasswordAllowed"] is True)
    smtp = realm.get("smtpServer") or {}
    check("smtp host set", bool(smtp.get("host")), smtp.get("host", ""))
    check("smtp from set", bool(smtp.get("from")), smtp.get("from", ""))
    check(
        "smtp auth matches env",
        (smtp.get("auth") or "false").lower() == (env.get("KC_SMTP_AUTH") or "false").lower(),
        smtp.get("auth", ""),
    )
    actions = {a["alias"]: a for a in kc.get(env, token, f"/admin/realms/{kc.REALM}/authentication/required-actions")}
    check("passkey registration action on", actions.get("webauthn-register-passwordless", {}).get("enabled") is True)

    # Группы людей: staff / mentors / students / contour. Потоки LMS не в IdP.
    paths = {g["path"] for g in kc.get(env, token, f"/admin/realms/{kc.REALM}/groups")}
    check(
        "people groups",
        {"/staff", "/mentors", "/students", "/contour"} <= paths,
        ", ".join(sorted(paths)),
    )
    check("no stream-* IdP groups", not any(p.startswith("/stream-") for p in paths), ", ".join(sorted(paths)))

    # Клиенты по закрытой матрице: Sonar только SAML, остальные OIDC.
    clients = {c["clientId"]: c for c in kc.get(env, token, f"/admin/realms/{kc.REALM}/clients")}
    check("sonar speaks SAML", clients.get("sonar", {}).get("protocol") == "saml")
    check("jira speaks SAML", clients.get("https://jira.qa.guru", {}).get("protocol") == "saml")
    check("confluence speaks SAML", clients.get("https://confluence.qa.guru", {}).get("protocol") == "saml")
    for name in ("grafana", "jenkins", "gitlab", "testops", "oauth2-proxy", "learn", "autotests-ai"):
        check(f"{name} speaks OIDC", clients.get(name, {}).get("protocol") == "openid-connect")
    dest = clients.get("autotests-ai") or {}
    check("autotests-ai is confidential", dest.get("publicClient") is False)
    dest_uris = dest.get("redirectUris") or []
    check(
        "autotests-ai prod dest-cloud callback",
        "https://autotests.ai/oauth/idp/callback" in dest_uris,
        ", ".join(dest_uris),
    )
    check(
        "autotests-ai stand dest-cloud callback",
        "http://localhost:8081/oauth/idp/callback" in dest_uris
        or "http://127.0.0.1:8081/oauth/idp/callback" in dest_uris,
        ", ".join(dest_uris),
    )
    svc = clients.get("svc-provisioning", {})
    check(
        "service account has no interactive login",
        svc.get("serviceAccountsEnabled") is True and svc.get("standardFlowEnabled") is False,
    )
    selenoid_sa = clients.get("svc-selenoid-token", {})
    check(
        "selenoid token SA has no interactive login",
        selenoid_sa.get("serviceAccountsEnabled") is True and selenoid_sa.get("standardFlowEnabled") is False,
        "run ensure-selenoid-token-client.py",
    )
    rm = clients.get("realm-management") or {}
    if svc.get("id") and rm.get("id"):
        prov_sa = kc.get(
            env,
            token,
            f"/admin/realms/{kc.REALM}/clients/{svc['id']}/service-account-user",
        )
        prov_roles = {
            r["name"]
            for r in (
                kc.get(
                    env,
                    token,
                    f"/admin/realms/{kc.REALM}/users/{prov_sa['id']}/role-mappings/clients/{rm['id']}",
                )
                or []
            )
        }
        check("svc-provisioning still manage-users", "manage-users" in prov_roles, ",".join(sorted(prov_roles)))
    else:
        check("svc-provisioning still manage-users", False, "client missing")
    if selenoid_sa.get("id") and rm.get("id"):
        token_sa = kc.get(
            env,
            token,
            f"/admin/realms/{kc.REALM}/clients/{selenoid_sa['id']}/service-account-user",
        )
        token_roles = {
            r["name"]
            for r in (
                kc.get(
                    env,
                    token,
                    f"/admin/realms/{kc.REALM}/users/{token_sa['id']}/role-mappings/clients/{rm['id']}",
                )
                or []
            )
        }
        check(
            "selenoid token SA is view-only",
            "view-users" in token_roles and "query-users" in token_roles and "manage-users" not in token_roles,
            ",".join(sorted(token_roles)) or "run ensure-selenoid-token-client.py",
        )
    else:
        check("selenoid token SA is view-only", False, "run ensure-selenoid-token-client.py")

    # GitHub — brokering, не прямая интеграция в каждом сервисе (ADR 017 п. 7).
    idps = {i["alias"]: i for i in kc.get(env, token, f"/admin/realms/{kc.REALM}/identity-provider/instances")}
    github = idps.get("github", {})
    check("github brokering enabled", github.get("enabled") is True)
    configured = github.get("config", {}).get("clientId", "").strip()
    check(
        "github OAuth App credentials filled",
        bool(configured) and "not-configured" not in configured,
        "fill KC_GITHUB_CLIENT_ID/SECRET in dev/.env from your own test OAuth App" if "not-configured" in configured else configured,
    )
    gh_mappers = {
        m.get("name"): m
        for m in (kc.get(env, token, f"/admin/realms/{kc.REALM}/identity-provider/instances/github/mappers") or [])
    }
    students_mapper = gh_mappers.get("github-to-students") or {}
    check(
        "github first login joins /students",
        students_mapper.get("identityProviderMapper") == "oidc-hardcoded-group-idp-mapper"
        and (students_mapper.get("config") or {}).get("group") == "/students",
        json.dumps(students_mapper.get("config") or {}, ensure_ascii=False),
    )

    # Единственный шаг, который нельзя проверить скриптом целиком: живой вход через GitHub.
    # Человек кликает кнопку один раз, а доказательство ищется на сервере — federated identity.
    all_users = kc.get(env, token, f"/admin/realms/{kc.REALM}/users?briefRepresentation=true&max=200")
    linked = [
        u["username"]
        for u in all_users
        if any(
            fi["identityProvider"] == "github"
            for fi in kc.get(env, token, f"/admin/realms/{kc.REALM}/users/{u['id']}/federated-identity")
        )
    ]
    check("someone signed in through GitHub", bool(linked), ", ".join(linked) or "no account linked yet")

    # Группы в claim: у Keycloak они уезжают с ведущим «/» — Jenkins Role Strategy ждёт ровно так.
    users = kc.get(env, token, f"/admin/realms/{kc.REALM}/users?username=student-demo&exact=true")
    if users:
        uid = users[0]["id"]
        jenkins_uuid = clients["jenkins"]["id"]
        example = kc.get(
            env,
            token,
            f"/admin/realms/{kc.REALM}/clients/{jenkins_uuid}/evaluate-scopes/generate-example-id-token?userId={uid}",
        )
        groups = example.get("groups") or []
        check("OIDC groups claim keeps the leading slash", "/students" in groups, json.dumps(groups))
    else:
        check("OIDC groups claim keeps the leading slash", False, "student-demo missing — run seed-stand-users.py")

    sonar_mappers = {m["name"]: m for m in clients.get("sonar", {}).get("protocolMappers", [])}
    check(
        "SAML groups without slash for Sonar",
        sonar_mappers.get("groups", {}).get("config", {}).get("full.path") == "false",
    )

    # Realm as code: в git не должно быть ни одного секрета.
    leaks = secrets_in_realm_file()
    check("no secrets in the tracked realm file", not leaks, ", ".join(leaks))

    # Selenoid personal token — User Profile, Account Console. Ферма в этом окне не трогается.
    profile = kc.get(env, token, f"/admin/realms/{kc.REALM}/users/profile") or {}
    token_attr = next(
        (a for a in (profile.get("attributes") or []) if a.get("name") == "selenoidToken"),
        {},
    )
    token_perms = token_attr.get("permissions") or {}
    token_len = (token_attr.get("validations") or {}).get("length") or {}
    check("selenoidToken in user profile", bool(token_attr), "run apply-user-profile.py")
    check("selenoidToken editable by user", "user" in (token_perms.get("edit") or []))
    check("selenoidToken min length 20", token_len.get("min") == 20, str(token_len.get("min")))
    check("unmanaged attributes unset", profile.get("unmanagedAttributePolicy") in (None, "DISABLED"), str(profile.get("unmanagedAttributePolicy")))

    width = max(len(n) for n, _, _ in checks)
    for name, ok, detail in checks:
        print(f"{'ok  ' if ok else 'FAIL'}  {name.ljust(width)}  {detail}".rstrip())
    failed = [n for n, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
