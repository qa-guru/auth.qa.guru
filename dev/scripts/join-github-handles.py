#!/usr/bin/env python
"""Classify GitHub → school handle (ADR 017 §5). Dry-run only.

Inputs: homework GitHub map, Jenkins SCM inventory, live handles, and the
email-relink plan from relink-github-duplicates.py. Does not POST federated
identity, disable users, or rewrite ghost emails.

    python scripts/join-github-handles.py --prod --dry-run
    python scripts/join-github-handles.py --self-test

Tiers: A login==handle and unique in HW or SCM; B unique email-match without
HW/SCM contradiction; C unique xor (review, no apply); D conflicts (skip).

Apply is relink-github-duplicates.py --prod --from-join (A+B allowlist).
This script stays dry-run only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc  # noqa: E402

PROVIDER = "github"
ETALON_ORGS = frozenset({"qa-guru", "autotests-ai", "autotests-cloud"})
LIVE_STUDENT = "live-student"
STAFF_BUCKET = "staff"
REPO = Path(__file__).resolve().parents[6]
DEFAULT_HW = REPO / "projects/qa-guru-home/scripts/out/github-jenkins-hw-map.json"
DEFAULT_SCM = Path.home() / ".config/jenkins/scm-inventory.json"
DEFAULT_MAP = Path.home() / ".config/jenkins/p4-inventory/username-map.json"
DEFAULT_OUT = Path.home() / ".config/auth-qa-guru/github-handle-join-dry-run.json"


def _load_relink() -> Any:
    path = Path(__file__).with_name("relink-github-duplicates.py")
    spec = importlib.util.spec_from_file_location("relink_github_duplicates", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dry-run GitHub → handle join (no realm writes)")
    p.add_argument("--prod", action="store_true", help=f"Use {kc.PROD_ENV}")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--only", action="append", default=[], help="Limit to this GitHub or handle")
    p.add_argument("--hw-map", type=Path, default=DEFAULT_HW)
    p.add_argument("--scm", type=Path, default=DEFAULT_SCM)
    p.add_argument("--username-map", type=Path, default=DEFAULT_MAP)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args(argv)


def load_buckets(path: Path) -> tuple[set[str], set[str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    students: set[str] = set()
    staff: set[str] = set()
    for row in rows:
        name = str(row.get("jenkins_username") or "").strip().lower()
        if not name:
            continue
        bucket = row.get("bucket")
        if bucket == LIVE_STUDENT:
            students.add(name)
        elif bucket == STAFF_BUCKET:
            staff.add(name)
    return students, staff


def hw_owner_counts(payload: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for owner, rec in (payload.get("github_owner_index") or {}).items():
        key = str(owner).strip().lower()
        if not key:
            continue
        out[key] = int(rec.get("n_students") or 0)
    return out


def scm_github_to_handles(
    payload: dict[str, Any],
    live_student: set[str],
    staff: set[str],
) -> dict[str, set[str]]:
    """student-remote GitHub owner → live-student handles.

    Staff SID on a job is folder/admin ownership, not the student's GitHub.
    If the owner string is itself a live handle, that handle wins.
    """
    g2h: dict[str, set[str]] = defaultdict(set)
    jobs = payload.get("jobs") or {}
    for rec in jobs.values():
        if not isinstance(rec, dict) or rec.get("etalon"):
            continue
        owners = [
            str(o).strip().lower()
            for o in (rec.get("github_owners") or [])
            if str(o).strip() and str(o).strip().lower() not in ETALON_ORGS
        ]
        if not owners:
            continue
        jo = str(rec.get("jenkins_owner") or "").strip().lower()
        for owner in owners:
            g2h.setdefault(owner, set())
            if owner in live_student or owner in staff:
                g2h[owner].add(owner)
            elif jo in live_student:
                g2h[owner].add(jo)
    return dict(g2h)


def _gh_login(person: dict[str, Any]) -> str:
    gh = person.get("github") or []
    if not gh:
        return ""
    return str((gh[0] or {}).get("userName") or "").strip().lower()


def classify(
    *,
    hw_counts: dict[str, int],
    scm_handles: dict[str, set[str]],
    live_student: set[str],
    staff: set[str],
    email_actions: list[dict[str, Any]],
    people: list[dict[str, Any]],
    only: set[str],
) -> dict[str, Any]:
    by_name = {str(p.get("username") or "").strip().lower(): p for p in people}
    github_users = {name: p for name, p in by_name.items() if p.get("github")}
    universe = set(hw_counts) | set(scm_handles)
    for p in people:
        login = _gh_login(p)
        if login:
            universe.add(login)
    for row in email_actions:
        for key in ("from", "to", "username"):
            val = str(row.get(key) or "").strip().lower()
            if val:
                universe.add(val)
        gh = row.get("github") or {}
        login = str(gh.get("userName") or "").strip().lower()
        if login:
            universe.add(login)

    def wanted(github: str, handle: str = "") -> bool:
        if not only:
            return True
        keys = {github, handle}
        return bool(keys & {x.strip().lower() for x in only if x.strip()})

    marked: dict[str, dict[str, Any]] = {}

    def put(github: str, tier: str, reason: str, handle: str = "") -> None:
        if not github or github in marked:
            return
        if not wanted(github, handle):
            return
        row: dict[str, Any] = {"tier": tier, "reason": reason, "github": github}
        if handle:
            row["handle"] = handle
        marked[github] = row

    # D: staff github login, shared HW, two SCM handles
    for github in list(universe):
        if github in staff:
            put(github, "D", "skip_staff_handle", github)
        elif hw_counts.get(github, 0) >= 2:
            put(github, "D", "hw_shared")
        elif len(scm_handles.get(github) or []) >= 2:
            put(github, "D", "two_handles")

    # D: one live-student handle with two candidate githubs
    handle_githubs: dict[str, set[str]] = defaultdict(set)
    for github, handles in scm_handles.items():
        if github in marked or len(handles) != 1:
            continue
        handle_githubs[next(iter(handles))].add(github)
    for github in live_student:
        if github not in marked and (hw_counts.get(github, 0) == 1 or github in scm_handles):
            handle_githubs[github].add(github)
    for handle, gh_set in handle_githubs.items():
        if handle in staff:
            continue
        extras = gh_set - {handle}
        if len(gh_set) < 2 or not extras:
            continue
        for github in extras:
            put(github, "D", "two_github", handle)

    # email-relink skips → D
    for row in email_actions:
        action = row.get("action") or ""
        src = str(row.get("from") or row.get("username") or "").strip().lower()
        dest = str(row.get("to") or "").strip().lower()
        gh = str((row.get("github") or {}).get("userName") or "").strip().lower() or src
        if action == "skip_staff_email":
            put(gh or src, "D", "staff_email", dest)
        elif action == "skip_target_has_github":
            put(gh or src, "D", "target_already_has_github", dest)

    # A: github login == live-student handle and unique in HW or SCM
    for github in live_student:
        if github in marked:
            continue
        hw_unique = hw_counts.get(github, 0) == 1
        scm_set = scm_handles.get(github) or set()
        scm_unique = scm_set == {github}
        if not (hw_unique or scm_unique):
            continue
        if scm_set and github not in scm_set:
            put(github, "D", "two_handles", next(iter(scm_set)))
            continue
        person = by_name.get(github)
        sources = []
        if hw_unique:
            sources.append("hw")
        if scm_unique:
            sources.append("scm")
        if person is None:
            put(github, "A", "missing_kc", github)
            marked[github]["sources"] = sources
            marked[github]["kc"] = "missing"
            continue
        existing = _gh_login(person)
        if existing and existing != github:
            put(github, "D", "target_already_has_github", github)
            continue
        kc_state = "already_linked" if existing == github else "attach"
        put(github, "A", kc_state, github)
        marked[github]["sources"] = sources
        marked[github]["kc"] = kc_state

    # B: unique email merge, maps do not contradict
    for row in email_actions:
        if row.get("action") != "merge":
            continue
        github = str((row.get("github") or {}).get("userName") or row.get("from") or "").strip().lower()
        handle = str(row.get("to") or "").strip().lower()
        if not github or not handle:
            continue
        if github in marked:
            continue
        if handle in staff:
            put(github, "D", "skip_staff_handle", handle)
            continue
        if hw_counts.get(github, 0) >= 2:
            put(github, "D", "hw_shared", handle)
            continue
        scm_set = scm_handles.get(github) or set()
        if scm_set and handle not in scm_set:
            put(github, "D", "scm_contradicts_email", handle)
            continue
        if github in live_student and github != handle:
            put(github, "D", "login_is_other_handle", handle)
            continue
        target = by_name.get(handle)
        if target and _gh_login(target) and _gh_login(target) != github:
            put(github, "D", "target_already_has_github", handle)
            continue
        put(github, "B", "unique_email_match", handle)
        marked[github]["from"] = str(row.get("from") or github)

    # C: unique in HW xor SCM, no second source — review, no apply
    for github in universe:
        if github in marked:
            continue
        hw_n = hw_counts.get(github, 0)
        scm_set = scm_handles.get(github) or set()
        in_hw = github in hw_counts
        in_scm = github in scm_handles
        hw_unique = hw_n == 1
        scm_unique = len(scm_set) == 1
        if hw_unique and not in_scm:
            handle = github if github in live_student else ""
            put(github, "C", "xor_hw_only", handle)
            continue
        if scm_unique and not in_hw:
            handle = next(iter(scm_set))
            prior = marked.get(handle)
            if prior and prior["tier"] == "A":
                put(github, "D", "two_github", handle)
                continue
            target = by_name.get(handle)
            if target and _gh_login(target) and _gh_login(target) != github:
                put(github, "D", "target_already_has_github", handle)
                continue
            put(github, "C", "xor_scm_only", handle)
            continue
        if in_hw and in_scm and hw_unique and not scm_set:
            put(github, "C", "both_seen_no_handle")
            continue
        if in_scm and not in_hw and not scm_set:
            put(github, "C", "xor_scm_only_no_handle")
            continue

    # leftover Keycloak github ghosts
    for name, person in github_users.items():
        login = _gh_login(person) or name
        if login in marked or name in marked:
            continue
        if not wanted(login, name):
            continue
        put(login, "C", "kc_only")

    rows = list(marked.values())
    skip_reasons = Counter(r["reason"] for r in rows if r["tier"] in {"C", "D"})
    counts = {
        "A": sum(1 for r in rows if r["tier"] == "A"),
        "B": sum(1 for r in rows if r["tier"] == "B"),
        "C": sum(1 for r in rows if r["tier"] == "C"),
        "D": sum(1 for r in rows if r["tier"] == "D"),
        "A_attach": sum(1 for r in rows if r.get("kc") == "attach"),
        "A_already_linked": sum(1 for r in rows if r.get("kc") == "already_linked"),
        "A_missing_kc": sum(1 for r in rows if r.get("kc") == "missing"),
    }
    c_reasons = Counter(r["reason"] for r in rows if r["tier"] == "C")
    d_reasons = Counter(r["reason"] for r in rows if r["tier"] == "D")
    a_handles = sorted({r["handle"] for r in rows if r["tier"] == "A" and r.get("handle")})
    c_handles = sorted(
        {
            r["handle"]
            for r in rows
            if r["tier"] == "C" and r.get("handle") and r["reason"] in {"xor_hw_only", "xor_scm_only"}
        }
    )
    return {
        "rows": rows,
        "counts": counts,
        "skip_reasons": dict(skip_reasons),
        "C_reasons": dict(c_reasons),
        "D_reasons": dict(d_reasons),
        "A_handles": a_handles,
        "C_handles": c_handles,
    }


def public_stdout(plan: dict[str, Any]) -> dict[str, Any]:
    """Chat-safe: counts, A/C handles (unique, no email). No emails, no B names."""
    return {
        "ok": plan.get("ok"),
        "dry_run": True,
        "apply": False,
        "prod": plan.get("prod"),
        "base_url": plan.get("base_url"),
        "live_student": plan.get("live_student"),
        "staff": plan.get("staff"),
        "email_relink": plan.get("email_relink"),
        "counts": plan.get("counts"),
        "C_reasons": plan.get("C_reasons"),
        "D_reasons": plan.get("D_reasons"),
        "skip_reasons": plan.get("skip_reasons"),
        "A_handles": plan.get("A_handles"),
        "C_handles": plan.get("C_handles"),
        "federated_github": plan.get("federated_github"),
        "wrote": plan.get("wrote"),
    }


def file_payload(plan: dict[str, Any], classified: dict[str, Any]) -> dict[str, Any]:
    rows_out = []
    for r in classified["rows"]:
        item = {k: v for k, v in r.items() if k != "github" or r["tier"] in {"B", "D"}}
        if r["tier"] == "A":
            item = {k: v for k, v in r.items() if k != "github"}
        if r["tier"] == "C":
            item = {k: v for k, v in r.items() if k not in {"github"}}
        if r["tier"] == "B":
            item = {
                "tier": "B",
                "reason": r.get("reason"),
                "to": r.get("handle"),
                "from": r.get("from"),
            }
        rows_out.append(item)
    out = {k: v for k, v in plan.items() if k != "rows"}
    out["rows"] = rows_out
    return out


def self_test() -> int:
    live = {"alice", "bob", "erin"}
    staff = {"root"}
    hw = {"alice": 1, "carol": 1, "sharedgh": 2, "xoronly": 1, "bothnh": 1}
    scm = {
        "alice": {"alice"},
        "bob": {"bob"},
        "sharedgh": {"alice"},
        "twoh": {"alice", "bob"},
        "bothnh": set(),
        "scmonly": {"bob"},
        "erinonly": {"erin"},
    }
    people = [
        {"username": "alice", "github": [], "groups": {"/students"}, "id": "1", "email": "a@x"},
        {
            "username": "bob",
            "github": [{"userId": "9", "userName": "bob"}],
            "groups": {"/students"},
            "id": "2",
            "email": "b@x",
        },
        {
            "username": "ghost",
            "github": [{"userId": "8", "userName": "ghostgh"}],
            "groups": set(),
            "id": "3",
            "email": "g@x",
        },
        {
            "username": "dave",
            "github": [],
            "groups": {"/students"},
            "id": "4",
            "email": "g@x",
        },
        {
            "username": "rooted",
            "github": [{"userId": "7", "userName": "evil"}],
            "groups": set(),
            "id": "5",
            "email": "staff@x",
        },
        {
            "username": "root",
            "github": [],
            "groups": {"/staff"},
            "id": "6",
            "email": "staff@x",
        },
    ]
    email_actions = [
        {
            "action": "merge",
            "from": "ghost",
            "to": "dave",
            "github": {"userId": "8", "userName": "ghostgh"},
        },
        {"action": "skip_staff_email", "from": "rooted", "reason": "same email as staff/mentor"},
        {
            "action": "skip_target_has_github",
            "from": "other",
            "to": "bob",
            "reason": "target already has a GitHub identity",
        },
    ]
    got = classify(
        hw_counts=hw,
        scm_handles=scm,
        live_student=live,
        staff=staff,
        email_actions=email_actions,
        people=people,
        only=set(),
    )
    def one(pred) -> dict[str, Any]:
        hits = [r for r in got["rows"] if pred(r)]
        assert len(hits) == 1, hits
        return hits[0]

    alice = one(lambda r: r["tier"] == "A" and r.get("handle") == "alice")
    bob = one(lambda r: r["tier"] == "A" and r.get("handle") == "bob")
    assert alice["kc"] == "attach", alice
    assert bob["kc"] == "already_linked", bob
    assert one(lambda r: r["tier"] == "B" and r.get("handle") == "dave")
    assert one(lambda r: r["tier"] == "C" and r["reason"] == "xor_hw_only" and r.get("github") == "xoronly")
    assert one(lambda r: r["tier"] == "C" and r["reason"] == "xor_scm_only" and r.get("handle") == "erin")
    assert one(lambda r: r["tier"] == "C" and r["reason"] == "both_seen_no_handle")
    assert one(lambda r: r["tier"] == "D" and r["reason"] == "hw_shared")
    assert one(lambda r: r["tier"] == "D" and r["reason"] == "two_handles")
    assert one(lambda r: r["tier"] == "D" and r["reason"] == "staff_email")
    assert one(lambda r: r["tier"] == "D" and r["reason"] == "target_already_has_github")
    assert one(lambda r: r["tier"] == "D" and r["reason"] == "two_github" and r.get("github") == "scmonly")
    print(json.dumps({"ok": True, "self_test": True, "counts": got["counts"], "C_reasons": got["C_reasons"], "D_reasons": got["D_reasons"]}, indent=2))
    return 0


def count_github_links(env: dict[str, str], token: str, relink: Any) -> dict[str, int]:
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
        fed = relink.federated(env, token, str(u["id"]))
        links += sum(1 for f in fed if f.get("identityProvider") == PROVIDER)
    return {"users": len(users), "links": links}


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    if ns.self_test:
        return self_test()
    if not ns.dry_run:
        raise SystemExit("join-github-handles.py is dry-run only; apply via relink --prod --from-join")
    if not ns.hw_map.is_file():
        raise SystemExit(f"missing hw-map {ns.hw_map}")
    if not ns.scm.is_file():
        raise SystemExit(f"missing scm {ns.scm}")
    if not ns.username_map.is_file():
        raise SystemExit(f"missing username-map {ns.username_map}")

    live_student, staff = load_buckets(ns.username_map)
    hw_counts = hw_owner_counts(json.loads(ns.hw_map.read_text(encoding="utf-8")))
    scm_handles = scm_github_to_handles(json.loads(ns.scm.read_text(encoding="utf-8")), live_student, staff)
    only = {x.strip() for x in ns.only if x.strip()}
    relink = _load_relink()
    env = kc.load_env(kc.PROD_ENV if ns.prod else None)
    token = kc.admin_token(env)
    before = count_github_links(env, token, relink)
    people = relink.load_people(env, token)
    handles = live_student | staff
    email_actions = relink.plan_actions(people, handles, only)
    classified = classify(
        hw_counts=hw_counts,
        scm_handles=scm_handles,
        live_student=live_student,
        staff=staff,
        email_actions=email_actions,
        people=people,
        only=only,
    )
    after = count_github_links(env, token, relink)
    plan: dict[str, Any] = {
        "ok": True,
        "dry_run": True,
        "apply": False,
        "prod": ns.prod,
        "base_url": kc.base_url(env),
        "live_student": len(live_student),
        "staff": len(staff),
        "hw_owners": len(hw_counts),
        "scm_student_remote_owners": len(scm_handles),
        "only": sorted(only),
        "email_relink": {
            "merge": sum(1 for a in email_actions if a["action"] == "merge"),
            "join_students": sum(1 for a in email_actions if a["action"] == "join_students"),
            "skip": sum(1 for a in email_actions if str(a["action"]).startswith("skip_")),
        },
        "counts": classified["counts"],
        "C_reasons": classified["C_reasons"],
        "D_reasons": classified["D_reasons"],
        "skip_reasons": classified["skip_reasons"],
        "A_handles": classified["A_handles"],
        "C_handles": classified["C_handles"],
        "federated_github": {"before": before, "after": after},
        "realm_unchanged": before == after,
    }
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    payload = file_payload(plan, classified)
    ns.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ns.out.chmod(0o600)
    plan["wrote"] = str(ns.out)
    print(json.dumps(public_stdout(plan), ensure_ascii=False, indent=2))
    if before != after:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
