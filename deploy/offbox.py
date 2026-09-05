#!/usr/bin/env python3
"""Off-box backup receiver for auth.qa.guru: provision, verify, prove restore.

Why this exists: P2a shipped a pg_dump timer whose S3 leg was optional, and the
Hetzner keys it was written against had already been revoked. The result was a
realm with 99 live people whose only backups were two 250 KB dumps of an *empty*
realm sitting on the same disk as the database. "Off-box backup" was a line in a
plan, not a file anywhere else.

Receiver is Selectel Object Storage (pool ru-1, path-style). The writer is a
dedicated service user with role `s3.admin` scoped to one project: it can manage
buckets there and nothing else in the account. `s3.user` / `s3.bucket.user` would
also work but only alongside a bucket policy, which is a second thing to get
wrong for no gain here.

Secrets never touch git, argv or the chat: the S3 key is written straight to
/etc/keycloak/s3.env (root 600) over stdin, mirroring ADR 017 п. 12.

  offbox.py provision --apply       create service user + key + bucket + s3.env
  offbox.py status                  is off-box configured, how old is the newest object
  offbox.py verify                  run the real timer unit, assert the object landed
  offbox.py restore-check           restore the newest OFF-BOX object into a scratch db
  offbox.py restore-check --from-file PATH
  offbox.py set-retention --days 30
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[assignment]

HOST = os.environ.get("AUTH_DEPLOY_HOST", "auth-qa-guru")
IAM_FILE = Path(os.environ.get("SELECTEL_IAM", Path.home() / ".config/selectel-iam.json"))
PROJECT_ID = os.environ.get("SELECTEL_PROJECT_ID", "867a0fed571e4ecdae9dc5a73cd99046")
IDENTITY = "https://cloud.api.selcloud.ru/identity/v3"
IAM_API = "https://api.selectel.ru/iam/v1"

SERVICE_USER = "auth-qa-guru-backup"
BUCKET = os.environ.get("S3_BUCKET", "qa-guru-idp-backup")
POOL = os.environ.get("S3_POOL", "ru-1")
ENDPOINT = f"https://s3.{POOL}.storage.selcloud.ru"
PREFIX = "auth-qa-guru/"
S3_ENV = "/etc/keycloak/s3.env"
# Local dumps roll at 14 days (keycloak-pg-dump.sh); off-box keeps longer, since
# the whole point is surviving loss of the host.
RETENTION_DAYS = 30
EXPECT_HUMANS = int(os.environ.get("AUTH_PEOPLE_FLOOR", "99"))
SCRATCH_DB = "keycloak_offbox_check"


def ctx() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def ssh(script: str, *, check: bool = True) -> str:
    proc = subprocess.run(
        ["ssh", HOST, "bash", "-s"],
        input=f"{script}\n",
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        die(f"ssh failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    return proc.stdout.strip()


def ssh_write_secret(path: str, content: str) -> None:
    """Ship a secret over stdin. Never argv: argv shows up in ps and history."""
    script = f"umask 077; cat > {path}; chown root:root {path}; chmod 600 {path}"
    proc = subprocess.run(
        ["ssh", HOST, f"sudo bash -c {json.dumps(script)}"],
        input=content,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        die(f"could not write {path}: {proc.stderr.strip()[:300]}")


def http(url: str, *, method: str = "GET", token: str | None = None, body: dict | None = None) -> tuple[int, dict, dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx()) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"_raw": raw[:300].decode(errors="replace")}
        return exc.code, dict(exc.headers or {}), parsed


def iam_token() -> str:
    if not IAM_FILE.is_file():
        die(f"{IAM_FILE} missing")
    iam = json.loads(IAM_FILE.read_text())
    body = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {
                    "user": {
                        "name": iam["username"],
                        "domain": {"name": str(iam["account_id"])},
                        "password": iam["password"],
                    }
                },
            },
            "scope": {"domain": {"name": str(iam["account_id"])}},
        }
    }
    req = urllib.request.Request(
        f"{IDENTITY}/auth/tokens",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx()) as resp:
            token = resp.headers.get("X-Subject-Token")
            roles = sorted(r.get("name") for r in json.loads(resp.read())["token"].get("roles", []))
    except urllib.error.HTTPError as exc:
        die(f"IAM auth failed {exc.code}: {exc.read()[:200]!r}")
    if "iam.admin" not in roles and "account_owner" not in roles:
        die(
            "the Selectel identity in ~/.config/selectel-iam.json has roles "
            f"{roles} — issuing an S3 key needs iam.admin (or the Account Owner). "
            "Grant it in the panel: IAM → Users → zero-design → add role."
        )
    return token  # type: ignore[return-value]


# --- commands -------------------------------------------------------------
def cmd_provision(args: argparse.Namespace) -> int:
    token = iam_token()

    code, _, listing = http(f"{IAM_API}/service_users", token=token)
    if code != 200:
        die(f"list service users -> {code} {listing}")
    existing = next(
        (u for u in listing.get("users", listing.get("service_users", [])) if u.get("name") == SERVICE_USER),
        None,
    )

    if not args.apply:
        print("DRY RUN — nothing mutated. Would ensure:")
        print(f"  service user  {SERVICE_USER} (role s3.admin, project {PROJECT_ID})"
              f"{' — exists' if existing else ' — create'}")
        print(f"  bucket        {BUCKET} at {ENDPOINT}")
        print(f"  host file     {S3_ENV} (root 600)")
        print(f"  retention     off-box {args.retention_days} days, local 14 days")
        print("\nRe-run with --apply.")
        return 0

    if existing:
        user_id = existing["id"]
        print(f"service user {SERVICE_USER} exists ({user_id})")
    else:
        code, _, created = http(
            f"{IAM_API}/service_users",
            method="POST",
            token=token,
            body={
                "name": SERVICE_USER,
                "password": secrets.token_urlsafe(24),
                "enabled": True,
                "roles": [{"role_name": "s3.admin", "scope": "project", "project_id": PROJECT_ID}],
            },
        )
        if code not in (200, 201):
            die(f"create service user -> {code} {created}")
        user_id = created.get("id") or created.get("user", {}).get("id")
        print(f"service user {SERVICE_USER} created ({user_id})")

    code, _, cred = http(
        f"{IAM_API}/service_users/{user_id}/credentials",
        method="POST",
        token=token,
        body={"project_id": PROJECT_ID, "name": "auth-qa-guru-pg-dump"},
    )
    if code not in (200, 201):
        die(f"issue S3 key -> {code} {cred}")
    blob = cred.get("credential", cred)
    access = blob.get("access_key") or blob.get("access")
    secret = blob.get("secret_key") or blob.get("secret")
    if not access or not secret:
        die(f"S3 key response missing keys: {sorted(blob)}")
    print(f"S3 key issued (access key {access[:6]}…, secret withheld)")

    env = (
        "# Off-box receiver for the daily Keycloak dump. Root 600, never git.\n"
        f"# Selectel Object Storage, pool {POOL}, path-style. Writer is service\n"
        f"# user {SERVICE_USER} with role s3.admin scoped to one project.\n"
        f"S3_ENDPOINT={ENDPOINT}\n"
        f"S3_REGION={POOL}\n"
        f"S3_BUCKET={BUCKET}\n"
        f"S3_PREFIX={PREFIX}\n"
        f"S3_ACCESS_KEY={access}\n"
        f"S3_SECRET_KEY={secret}\n"
    )
    ssh_write_secret(S3_ENV, env)
    print(f"wrote {S3_ENV} on {HOST} (root 600)")

    print(ssh(f"set -e; sudo bash -c 'set -a; . {S3_ENV}; set +a; python3 /usr/local/sbin/s3.py make-bucket'"))
    print(ssh(f"set -e; sudo bash -c 'set -a; . {S3_ENV}; set +a; "
              f"python3 /usr/local/sbin/s3.py set-lifecycle --days {args.retention_days}'"))
    ssh(f"sudo rm -f /etc/keycloak/backup-local-only")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    configured = ssh(f"test -f {S3_ENV} && echo yes || echo no")
    print(f"off-box configured: {configured}")
    if configured != "yes":
        print(f"  (local-only marker: {ssh('test -f /etc/keycloak/backup-local-only && echo present || echo absent')})")
        return 1
    print(ssh(f"sudo bash -c 'set -a; . {S3_ENV}; set +a; python3 /usr/local/sbin/s3.py list'"))
    print("local dumps:")
    print(ssh("sudo ls -la /var/backups/keycloak/ | tail -5"))
    return 0


def cmd_verify(_: argparse.Namespace) -> int:
    """Run the real unit and prove a NEW object appeared off-box."""
    before = ssh(f"sudo bash -c 'set -a; . {S3_ENV}; set +a; python3 /usr/local/sbin/s3.py list' | wc -l")
    ssh("sudo systemctl start keycloak-pg-dump.service")
    log = ssh("sudo journalctl -u keycloak-pg-dump.service -n 15 --no-pager")
    print(log)
    if "Failed" in log or "FAIL" in log:
        die("the dump unit reported failure")
    after_raw = ssh(f"sudo bash -c 'set -a; . {S3_ENV}; set +a; python3 /usr/local/sbin/s3.py list'")
    print(after_raw)
    if int(after_raw.count("\n")) + 1 <= int(before):
        die("no new object off-box after running the unit")
    newest = after_raw.strip().splitlines()[-1].split()[-1]
    print(f"OK: timer uploaded {newest}")
    return 0


def cmd_restore_check(args: argparse.Namespace) -> int:
    """Restore an OFF-BOX artifact into a scratch db. Live keycloak db untouched."""
    remote_tmp = "/tmp/offbox-restore-check.dump"
    if args.from_file:
        src = Path(args.from_file).expanduser()
        if not src.is_file():
            die(f"{src} missing")
        print(f"source: local off-box copy {src} ({src.stat().st_size} bytes)")
        subprocess.run(["ssh", HOST, f"cat > {remote_tmp}"], input=src.read_bytes(), check=True)
    else:
        listing = ssh(f"sudo bash -c 'set -a; . {S3_ENV}; set +a; python3 /usr/local/sbin/s3.py list'")
        if not listing.strip():
            die("no objects off-box to restore from")
        newest = listing.strip().splitlines()[-1].split()[-1]
        print(f"source: off-box object {newest}")
        ssh(
            f"sudo bash -c 'set -a; . {S3_ENV}; set +a; "
            f"python3 /usr/local/sbin/s3.py get {newest} {remote_tmp}' && sudo chmod 644 {remote_tmp}"
        )

    out = ssh(
        f"""
set -e
sudo -u postgres dropdb --if-exists {SCRATCH_DB}
sudo -u postgres createdb -O keycloak {SCRATCH_DB}
sudo cp {remote_tmp} /var/lib/postgresql/offbox-check.dump
sudo chown postgres:postgres /var/lib/postgresql/offbox-check.dump
sudo -u postgres pg_restore --exit-on-error -d {SCRATCH_DB} /var/lib/postgresql/offbox-check.dump
sudo -u postgres psql -d {SCRATCH_DB} -Atc "select count(*) from user_entity u join realm r on r.id=u.realm_id where r.name='qaguru' and u.service_account_client_link is null;"
sudo -u postgres psql -d {SCRATCH_DB} -Atc "select g.name||'='||count(m.user_id) from keycloak_group g join realm r on r.id=g.realm_id left join user_group_membership m on m.group_id=g.id where r.name='qaguru' group by g.name order by g.name;"
sudo -u postgres psql -d {SCRATCH_DB} -Atc "select count(distinct c.user_id) from credential c join user_entity u on u.id=c.user_id join realm r on r.id=u.realm_id where r.name='qaguru';"
sudo -u postgres psql -d {SCRATCH_DB} -Atc "select coalesce(string_agg(c.client_id,',' order by c.client_id),'') from client c join realm r on r.id=c.realm_id where r.name='qaguru' and c.client_id in ('jenkins','sonar','oauth2-proxy');"
sudo -u postgres dropdb {SCRATCH_DB}
sudo rm -f /var/lib/postgresql/offbox-check.dump {remote_tmp}
"""
    )
    lines = [l for l in out.splitlines() if l.strip()]
    humans = int(lines[0])
    groups = dict(l.split("=") for l in lines[1:-2])
    creds = int(lines[-2])
    clients = lines[-1]

    print(f"restored humans: {humans} (floor {args.expect_humans})")
    print(f"restored groups: {groups}")
    print(f"restored password credentials: {creds}")
    print(f"federated clients in copy: {clients}")

    ok = True
    if humans < args.expect_humans:
        print(f"FAIL: {humans} humans restored, expected >= {args.expect_humans}", file=sys.stderr)
        ok = False
    for required in ("staff", "mentors", "students"):
        if required not in groups:
            print(f"FAIL: group {required} missing from the restored copy", file=sys.stderr)
            ok = False
    for required in ("jenkins", "sonar", "oauth2-proxy"):
        if required not in clients:
            print(f"FAIL: client {required} missing from the restored copy", file=sys.stderr)
            ok = False
    if creds < args.expect_humans:
        print(f"FAIL: {creds} credentials restored — people would restore without passwords", file=sys.stderr)
        ok = False
    print("OK: off-box artifact restores with people, groups and credentials" if ok else "restore check FAILED")
    return 0 if ok else 1


def cmd_set_retention(args: argparse.Namespace) -> int:
    print(ssh(f"sudo bash -c 'set -a; . {S3_ENV}; set +a; "
              f"python3 /usr/local/sbin/s3.py set-lifecycle --days {args.days}'"))
    print(ssh(f"sudo bash -c 'set -a; . {S3_ENV}; set +a; python3 /usr/local/sbin/s3.py get-lifecycle'"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("provision")
    p.add_argument("--apply", action="store_true", help="mutate; default is dry-run")
    p.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    p.set_defaults(fn=cmd_provision)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)

    p = sub.add_parser("restore-check")
    p.add_argument("--from-file", help="restore this local off-box copy instead of the newest S3 object")
    p.add_argument("--expect-humans", type=int, default=EXPECT_HUMANS)
    p.set_defaults(fn=cmd_restore_check)

    p = sub.add_parser("set-retention")
    p.add_argument("--days", type=int, required=True)
    p.set_defaults(fn=cmd_set_retention)

    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    rc = args.fn(args)
    print(f"\n{args.cmd} finished in {(datetime.now(timezone.utc) - started).seconds}s")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
