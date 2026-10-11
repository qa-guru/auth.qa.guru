#!/usr/bin/env python
"""Re-attach GitHub first-login ghosts onto the school handle.

duplicateEmailsAllowed (ADR 017 §5) makes Keycloak's Create User If Unique skip
the email check. GitHub login therefore JIT-creates a second person when the
GitHub login ≠ Jenkins/Keycloak handle. Mapper `github-to-students` still
puts the ghost in `/students`, so merge must run even then — otherwise
Jenkins/TestOps see `afoks` instead of handle `afok`.

    python scripts/relink-github-duplicates.py --prod --dry-run
    python scripts/relink-github-duplicates.py --prod
    python scripts/relink-github-duplicates.py --prod --only aukolova
    python scripts/relink-github-duplicates.py --self-test
    python scripts/relink-github-duplicates.py --prod --from-join --dry-run
    python scripts/relink-github-duplicates.py --prod --from-join

GitHub → handle join (hw-map + SCM + this email plan), dry-run only:

    python scripts/join-github-handles.py --prod --dry-run

A+B apply is this script with --from-join (join JSON allowlist). No POST
without GitHub userId. No C/D. Does not delete users. Does not add /contour
(TestOps seats). Auto-link by email only when the other side is a unique live
Jenkins handle.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

PROVIDER = "github"
STUDENTS = "/students"
STAFF_PATHS = frozenset({"/staff", "/mentors"})
LIVE_BUCKETS = frozenset({"live-student", "staff"})
USERNAME_MAP = Path.home() / ".config/jenkins/p4-inventory/username-map.json"
DISABLED_EMAIL_SUFFIX = ".github-orphan-disabled@noreply.auth.qa.guru"
DEFAULT_JOIN = Path.home() / ".config/auth-qa-guru/github-handle-join-dry-run.json"
DEFAULT_APPLY_OUT = Path.home() / ".config/auth-qa-guru/github-handle-join-apply.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Relink GitHub first-login duplicates onto school handles")
    p.add_argument("--prod", action="store_true", help=f"Use {kc.PROD_ENV} against KC_HOSTNAME")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", action="append", default=[], help="Limit to this GitHub or handle username")
    p.add_argument(
        "--from-join",
        type=Path,
        nargs="?",
        const=DEFAULT_JOIN,
        default=None,
        help="A+B allowlist from join dry-run JSON (default path if flag only); no C/D",
    )
    p.add_argument("--out", type=Path, default=DEFAULT_APPLY_OUT)
    p.add_argument("--self-test", action="store_true")
    p.add_argument(
        "--username-map",
        type=Path,
        default=USERNAME_MAP,
        help="Jenkins P4 username-map.json (live handles)",
    )
    return p.parse_args(argv)


def live_handles(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row["jenkins_username"]).strip().lower()
        for row in rows
        if row.get("bucket") in LIVE_BUCKETS and row.get("jenkins_username")
    }


def paginate_users(env: dict[str, str], token: str) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    first = 0
    while True:
        batch = kc.get(env, token, f"/admin/realms/{kc.REALM}/users?max=100&first={first}") or []
        users.extend(batch)
        if len(batch) < 100:
            return users
        first += 100
        if first > 20_000:
            raise SystemExit("user pagination ran away")


def group_paths(env: dict[str, str], token: str, user_id: str) -> list[str]:
    rows = kc.get(env, token, f"/admin/realms/{kc.REALM}/users/{user_id}/groups") or []
    return [g.get("path") or "" for g in rows]


def federated(env: dict[str, str], token: str, user_id: str) -> list[dict[str, Any]]:
    return kc.get(env, token, f"/admin/realms/{kc.REALM}/users/{user_id}/federated-identity") or []


def group_id(env: dict[str, str], token: str, path: str) -> str:
    name = path.lstrip("/")
    rows = kc.get(env, token, f"/admin/realms/{kc.REALM}/groups?search={name}") or []
    for g in rows:
        if g.get("path") == path:
            return str(g["id"])
    raise SystemExit(f"Keycloak group {path} missing")


def slim_user(u: dict[str, Any], groups: list[str], fed: list[dict[str, Any]]) -> dict[str, Any]:
    gh = [f for f in fed if f.get("identityProvider") == PROVIDER]
    return {
        "id": u.get("id"),
        "username": u.get("username"),
        "email": u.get("email"),
        "enabled": u.get("enabled"),
        "groups": groups,
        "github": [
            {"userId": f.get("userId"), "userName": f.get("userName")}
            for f in gh
        ],
    }


def plan_actions(
    people: list[dict[str, Any]],
    handles: set[str],
    only: set[str],
) -> list[dict[str, Any]]:
    by_email: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in people:
        email = (p.get("email") or "").strip().lower()
        if email:
            by_email[email].append(p)

    actions: list[dict[str, Any]] = []
    only_l = {x.strip().lower() for x in only if x.strip()}
    handle_set = {h.strip().lower() for h in handles}
    for orphan in people:
        if orphan["groups"] & STAFF_PATHS:
            continue
        if not orphan["github"]:
            continue
        username = str(orphan["username"] or "")
        uname = username.strip().lower()
        gh_login = str((orphan["github"][0] or {}).get("userName") or "")
        email_names = {str(t.get("username") or "") for t in by_email.get((orphan.get("email") or "").lower(), [])}
        names = {uname, gh_login.strip().lower()} | {n.strip().lower() for n in email_names}
        if only_l and not (names & only_l):
            continue
        email = (orphan.get("email") or "").strip().lower()
        others = [p for p in by_email.get(email, []) if p["id"] != orphan["id"]]
        if others and any(p["groups"] & STAFF_PATHS for p in others):
            actions.append(
                {
                    "action": "skip_staff_email",
                    "from": orphan["username"],
                    "reason": "same email as staff/mentor",
                }
            )
            continue
        live_others = [
            p
            for p in others
            if STUDENTS in p["groups"] and str(p.get("username") or "").strip().lower() in handle_set
        ]
        if (
            uname not in handle_set
            and len(live_others) == 1
            and str(live_others[0].get("username") or "").strip().lower() != uname
        ):
            target = live_others[0]
            if target["github"]:
                actions.append(
                    {
                        "action": "skip_target_has_github",
                        "from": username,
                        "to": target["username"],
                        "reason": "target already has a GitHub identity",
                    }
                )
                continue
            actions.append(
                {
                    "action": "merge",
                    "from": username,
                    "from_id": orphan["id"],
                    "to": target["username"],
                    "to_id": target["id"],
                    "github": orphan["github"][0],
                    "reason": "GitHub login ≠ handle; unique live Jenkins email match",
                }
            )
            continue
        if STUDENTS in orphan["groups"]:
            continue
        actions.append(
            {
                "action": "join_students",
                "username": username,
                "id": orphan["id"],
                "reason": (
                    "GitHub login is the Jenkins handle"
                    if uname in handle_set
                    else "GitHub-only account; no other live handle on this email"
                ),
            }
        )
    return actions


def apply_merge(env: dict[str, str], token: str, row: dict[str, Any]) -> None:
    gh = row["github"]
    kc.delete(
        env,
        token,
        f"/admin/realms/{kc.REALM}/users/{row['from_id']}/federated-identity/{PROVIDER}",
    )
    kc.post(
        env,
        token,
        f"/admin/realms/{kc.REALM}/users/{row['to_id']}/federated-identity/{PROVIDER}",
        {
            "identityProvider": PROVIDER,
            "userId": gh["userId"],
            "userName": gh["userName"],
        },
    )
    ghost = kc.get(env, token, f"/admin/realms/{kc.REALM}/users/{row['from_id']}")
    ghost["enabled"] = False
    ghost["emailVerified"] = False
    ghost["email"] = f"{row['from']}{DISABLED_EMAIL_SUFFIX}"
    ghost["requiredActions"] = []
    kc.put(env, token, f"/admin/realms/{kc.REALM}/users/{row['from_id']}", ghost)


def apply_join(env: dict[str, str], token: str, row: dict[str, Any], students_gid: str) -> None:
    kc.put(
        env,
        token,
        f"/admin/realms/{kc.REALM}/users/{row['id']}/groups/{students_gid}",
        None,
    )


def load_people(env: dict[str, str], token: str) -> list[dict[str, Any]]:
    people = []
    for u in paginate_users(env, token):
        groups = set(group_paths(env, token, str(u["id"])))
        fed = federated(env, token, str(u["id"]))
        people.append({**slim_user(u, sorted(groups), fed), "groups": groups})
    return people


def count_github_links(env: dict[str, str], token: str) -> dict[str, int]:
    users: list[dict[str, Any]] = []
    first = 0
    while True:
        batch = (
            kc.get(
                env,
                token,
                f"/admin/realms/{kc.REALM}/users?idpAlias={PROVIDER}&max=100&first={first}&briefRepresentation=true",
            )
            or []
        )
        users.extend(batch)
        if len(batch) < 100:
            break
        first += 100
        if first > 20_000:
            raise SystemExit("github idp pagination ran away")
    links = 0
    for u in users:
        fed = federated(env, token, str(u["id"]))
        links += sum(1 for f in fed if f.get("identityProvider") == PROVIDER)
    return {"users": len(users), "links": links}


def ab_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") or []
    a_rows = [r for r in rows if r.get("tier") == "A"]
    b_rows = [r for r in rows if r.get("tier") == "B"]
    only: set[str] = set()
    for r in a_rows:
        handle = str(r.get("handle") or "").strip().lower()
        if handle:
            only.add(handle)
    for r in b_rows:
        for key in ("from", "to", "handle"):
            val = str(r.get(key) or "").strip().lower()
            if val:
                only.add(val)
    return {
        "a": a_rows,
        "b": b_rows,
        "only": only,
        "C": sum(1 for r in rows if r.get("tier") == "C"),
        "D": sum(1 for r in rows if r.get("tier") == "D"),
        "counts": payload.get("counts") or {},
    }


def load_ab_allowlist(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"missing join JSON {path}")
    out = ab_from_payload(json.loads(path.read_text(encoding="utf-8")))
    out["path"] = str(path)
    return out


def plan_a_skips(people: list[dict[str, Any]], a_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {str(p.get("username") or "").strip().lower(): p for p in people}
    actions: list[dict[str, Any]] = []
    for r in a_rows:
        handle = str(r.get("handle") or "").strip().lower()
        if not handle:
            continue
        person = by_name.get(handle)
        if person is None:
            actions.append(
                {"action": "skip_missing_kc", "username": handle, "reason": "handle not in realm"}
            )
            continue
        gh = person.get("github") or []
        user_id = str((gh[0] or {}).get("userId") or "").strip() if gh else ""
        login = str((gh[0] or {}).get("userName") or "").strip().lower() if gh else ""
        if user_id and login == handle:
            actions.append(
                {
                    "action": "skip_already_linked",
                    "username": handle,
                    "reason": "github already on handle",
                }
            )
            continue
        actions.append(
            {
                "action": "skip_no_userid",
                "username": handle,
                "reason": "no GitHub userId in realm; no POST",
            }
        )
    return actions


def filter_email_to_ab(
    actions: list[dict[str, Any]],
    b_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    b_pairs = {
        (str(r.get("from") or "").strip().lower(), str(r.get("to") or "").strip().lower())
        for r in b_rows
    }
    out: list[dict[str, Any]] = []
    for row in actions:
        kind = row.get("action") or ""
        if kind == "join_students":
            continue
        if kind == "merge":
            pair = (
                str(row.get("from") or "").strip().lower(),
                str(row.get("to") or "").strip().lower(),
            )
            if pair not in b_pairs:
                raise SystemExit(
                    f"from-join: refusing merge outside B allowlist {row.get('from')}->{row.get('to')}"
                )
            gh = row.get("github") or {}
            if not str(gh.get("userId") or "").strip():
                raise SystemExit("from-join: refusing merge without GitHub userId")
            out.append(row)
            continue
        if kind.startswith("skip_"):
            out.append(row)
    return out


def slim_action(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"from_id", "to_id", "id", "github"}}


def public_from_join(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": plan.get("ok"),
        "dry_run": plan.get("dry_run"),
        "apply": plan.get("apply"),
        "prod": plan.get("prod"),
        "base_url": plan.get("base_url"),
        "from_join": plan.get("from_join"),
        "only": plan.get("only"),
        "join_counts": plan.get("join_counts"),
        "counts": plan.get("counts"),
        "federated_github": plan.get("federated_github"),
        "ghosts_deleted": plan.get("ghosts_deleted"),
        "posts": plan.get("posts"),
        "wrote": plan.get("wrote"),
        "A_handles": plan.get("A_handles"),
    }


def self_test() -> int:
    payload = {
        "counts": {"A": 2, "B": 1, "C": 1, "D": 1},
        "rows": [
            {"tier": "A", "handle": "alice", "kc": "attach"},
            {"tier": "A", "handle": "bob", "kc": "already_linked"},
            {"tier": "B", "from": "ghost", "to": "dave"},
            {"tier": "C", "reason": "xor_hw_only"},
            {"tier": "D", "reason": "hw_shared"},
        ],
    }
    ab = ab_from_payload(payload)
    assert ab["only"] == {"alice", "bob", "ghost", "dave"}, ab["only"]
    assert ab["C"] == 1 and ab["D"] == 1
    people = [
        {"username": "alice", "github": [], "id": "1"},
        {"username": "bob", "github": [{"userId": "9", "userName": "bob"}], "id": "2"},
    ]
    skips = plan_a_skips(people, ab["a"])
    by_action = {s["action"]: s["username"] for s in skips}
    assert by_action == {"skip_no_userid": "alice", "skip_already_linked": "bob"}, skips
    ok = filter_email_to_ab(
        [
            {"action": "merge", "from": "ghost", "to": "dave", "github": {"userId": "8", "userName": "ghostgh"}},
            {"action": "join_students", "username": "carol"},
        ],
        ab["b"],
    )
    assert len(ok) == 1 and ok[0]["action"] == "merge", ok
    try:
        filter_email_to_ab(
            [{"action": "merge", "from": "evil", "to": "alice", "github": {"userId": "1"}}],
            ab["b"],
        )
    except SystemExit:
        pass
    else:
        raise AssertionError("expected refuse merge outside B")
    afok_case = plan_actions(
        [
            {
                "id": "ghost",
                "username": "afoks",
                "email": "student@example.com",
                "groups": {"/students"},
                "github": [{"userId": "297368173", "userName": "afoks"}],
            },
            {
                "id": "handle",
                "username": "afok",
                "email": "student@example.com",
                "groups": {"/students"},
                "github": [],
            },
        ],
        {"afok"},
        {"afoks"},
    )
    assert len(afok_case) == 1 and afok_case[0]["action"] == "merge", afok_case
    assert afok_case[0]["from"] == "afoks" and afok_case[0]["to"] == "afok", afok_case
    print(json.dumps({"ok": True, "self_test": True, "only": sorted(ab["only"])}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    if ns.self_test:
        return self_test()
    if ns.from_join is not None and not ns.prod:
        raise SystemExit("--from-join is the prod A+B allowlist; pass --prod")

    handles = live_handles(ns.username_map)
    only = {x.strip() for x in ns.only if x.strip()}
    ab: dict[str, Any] | None = None
    if ns.from_join is not None:
        ab = load_ab_allowlist(ns.from_join)
        join_only = ab["only"]
        only_l = {x.lower() for x in only}
        if only_l:
            extra = only_l - join_only
            if extra:
                raise SystemExit(f"--only outside A+B allowlist: {sorted(extra)}")
            only = only_l & join_only
        else:
            only = set(join_only)
        if not only:
            raise SystemExit("A+B allowlist is empty")

    plan: dict[str, Any] = {
        "ok": True,
        "dry_run": ns.dry_run,
        "apply": bool(ns.from_join) and not ns.dry_run,
        "prod": ns.prod,
        "base_url": "https://auth.qa.guru" if ns.prod else "http://localhost:8543",
        "live_handles": len(handles),
        "only": sorted(only),
        "ghosts_deleted": 0,
    }
    env = kc.load_env(kc.PROD_ENV if ns.prod else None)
    plan["base_url"] = kc.base_url(env)
    token = kc.admin_token(env)
    before = count_github_links(env, token) if ab is not None else None
    people = load_people(env, token)
    actions = plan_actions(people, handles, only)
    a_skips: list[dict[str, Any]] = []
    if ab is not None:
        plan["from_join"] = ab["path"]
        plan["join_counts"] = {
            "A": len(ab["a"]),
            "B": len(ab["b"]),
            "C": ab["C"],
            "D": ab["D"],
        }
        a_rows = [r for r in ab["a"] if str(r.get("handle") or "").strip().lower() in only]
        plan["A_handles"] = sorted(str(r.get("handle") or "") for r in a_rows if r.get("handle"))
        actions = filter_email_to_ab(actions, ab["b"])
        a_skips = plan_a_skips(people, a_rows)
        actions = actions + a_skips

    plan["actions"] = [slim_action(a) for a in actions]
    plan["counts"] = {
        "merge": sum(1 for a in actions if a["action"] == "merge"),
        "join_students": sum(1 for a in actions if a["action"] == "join_students"),
        "skip": sum(1 for a in actions if str(a["action"]).startswith("skip_")),
        "skip_no_userid": sum(1 for a in actions if a["action"] == "skip_no_userid"),
        "skip_already_linked": sum(1 for a in actions if a["action"] == "skip_already_linked"),
        "skip_missing_kc": sum(1 for a in actions if a["action"] == "skip_missing_kc"),
    }
    if ab is not None and plan["counts"]["join_students"]:
        raise SystemExit("from-join: refusing join_students")

    posts = 0
    applied: list[dict[str, Any]] = []
    if not ns.dry_run:
        students_gid = None if ab is not None else group_id(env, token, STUDENTS)
        for row in actions:
            kind = row["action"]
            if kind == "merge":
                gh = row.get("github") or {}
                if not str(gh.get("userId") or "").strip():
                    raise SystemExit("refusing POST federated-identity without userId")
                apply_merge(env, token, row)
                posts += 1
            elif kind == "join_students":
                if ab is not None:
                    raise SystemExit("from-join: refusing join_students")
                apply_join(env, token, row, students_gid)
            elif str(kind).startswith("skip_"):
                pass
            else:
                raise SystemExit(f"unknown action {kind}")
            applied.append(
                {"action": kind, "from": row.get("from") or row.get("username"), "to": row.get("to")}
            )
        plan["applied"] = applied
    plan["posts"] = posts
    after = count_github_links(env, token) if ab is not None else None
    if before is not None and after is not None:
        plan["federated_github"] = {"before": before, "after": after}
        if ns.dry_run and before != after:
            return 2
        if not ns.dry_run and posts == 0 and before != after:
            raise SystemExit("federated github changed with 0 posts")

    if ab is not None:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        payload = public_from_join(plan)
        payload["actions"] = plan["actions"]
        ns.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ns.out.chmod(0o600)
        plan["wrote"] = str(ns.out)
        print(json.dumps(public_from_join(plan), ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
