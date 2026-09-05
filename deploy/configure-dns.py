#!/usr/bin/env python3
"""Selectel DNS for auth.qa.guru.

Replaces the stale CNAME → reflector.qa.guru with A → the IdP VM.
Default is dry-run. Mutates only with --apply.

  python3 deploy/configure-dns.py
  python3 deploy/configure-dns.py --apply
"""
from __future__ import annotations

import argparse
import json
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[assignment]

SELECTEL_API_TOKEN = Path.home() / ".config/selectel-api.token"
STATE_FILE = (
    Path(__file__).resolve().parents[4]
    / "infra-home"
    / "raw"
    / "selectel"
    / "auth-qa-guru"
    / "state.json"
)

ZONE = "qa.guru"
FQDN = "auth.qa.guru"
ZONE_ID = "d36f148a-c0d9-49c6-a712-a1db19c5a986"
PROJECT_ID = "867a0fed571e4ecdae9dc5a73cd99046"
DNS_API = "https://api.selectel.ru/domains/v2"
TTL = 300
SSH_HOST = "auth-qa-guru"


def ssl_ctx() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def http(
    method: str,
    url: str,
    headers: dict,
    body: dict | list | None = None,
) -> tuple[int, dict | list | None]:
    data = None if body is None else json.dumps(body).encode()
    hdr = dict(headers)
    if data is not None and "Content-Type" not in hdr:
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_ctx()) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload: dict | list | None = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = {"raw": raw[:800]}
        return exc.code, payload


def project_token() -> str:
    token = SELECTEL_API_TOKEN.read_text(encoding="utf-8").strip()
    code, data = http(
        "POST",
        "https://api.selectel.ru/vpc/resell/v2/tokens",
        {"X-Token": token, "Accept": "application/json"},
        {"token": {"project_id": PROJECT_ID}},
    )
    if code != 200 or not isinstance(data, dict):
        raise RuntimeError(f"Selectel project token: {code} {data}")
    return data["token"]["id"]


def sel_headers(token: str) -> dict:
    return {"X-Auth-Token": token, "Accept": "application/json"}


def list_rrsets(token: str, zone_id: str) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        code, data = http(
            "GET",
            f"{DNS_API}/zones/{zone_id}/rrset?limit=1000&offset={offset}",
            sel_headers(token),
        )
        if code != 200 or not isinstance(data, dict):
            raise RuntimeError(f"Selectel rrset: {code} {data}")
        out.extend(data.get("result") or [])
        nxt = data.get("next_offset")
        if not nxt:
            break
        offset = int(nxt)
    return out


def find_rr(rrsets: list[dict], name: str, rtype: str) -> dict | None:
    want = name if name.endswith(".") else name + "."
    for row in rrsets:
        if row.get("name") == want and row.get("type") == rtype:
            return row
    return None


def vm_ip() -> str:
    if STATE_FILE.is_file():
        ip = json.loads(STATE_FILE.read_text()).get("vm", {}).get("ip")
        if ip:
            return ip
    proc = subprocess.run(
        ["ssh", "-G", SSH_HOST],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("hostname "):
            return line.split()[1]
    raise RuntimeError("IdP IP missing — run provision-auth-qa-guru.py --apply first")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    ns = parser.parse_args()
    ip = vm_ip()
    token = project_token()
    rrsets = list_rrsets(token, ZONE_ID)
    name = FQDN + "."
    report: dict = {"apply": ns.apply, "zone": ZONE, "fqdn": FQDN, "ip": ip, "steps": []}

    cname = find_rr(rrsets, name, "CNAME")
    if cname is not None:
        label = f"CNAME {name}"
        if not ns.apply:
            report["steps"].append({"action": "would_delete", "rr": label, "id": cname.get("id")})
        else:
            code, data = http(
                "DELETE",
                f"{DNS_API}/zones/{ZONE_ID}/rrset/{cname['id']}",
                sel_headers(token),
            )
            if code not in (200, 204):
                raise RuntimeError(f"Selectel DELETE {label}: {code} {data}")
            report["steps"].append({"action": "deleted", "rr": label})
            rrsets = list_rrsets(token, ZONE_ID)

    existing = find_rr(rrsets, name, "A")
    body = {
        "name": name,
        "ttl": TTL,
        "type": "A",
        "records": [{"content": ip, "disabled": False}],
        "comment": "auth.qa.guru Keycloak IdP",
    }
    if existing is not None:
        old = sorted(x["content"] for x in existing.get("records") or [])
        if old == [ip] and int(existing.get("ttl") or 0) == TTL:
            report["steps"].append({"action": "noop", "rr": f"A {name}", "ip": ip})
        elif not ns.apply:
            report["steps"].append({"action": "would_update", "rr": f"A {name}", "from": old, "to": ip})
        else:
            code, data = http(
                "PATCH",
                f"{DNS_API}/zones/{ZONE_ID}/rrset/{existing['id']}",
                sel_headers(token),
                {"ttl": TTL, "records": [{"content": ip, "disabled": False}], "comment": body["comment"]},
            )
            if code not in (200, 204):
                raise RuntimeError(f"Selectel PATCH A {name}: {code} {data}")
            report["steps"].append({"action": "updated", "rr": f"A {name}", "to": ip})
    elif not ns.apply:
        report["steps"].append({"action": "would_create", "rr": f"A {name}", "to": ip})
    else:
        code, data = http("POST", f"{DNS_API}/zones/{ZONE_ID}/rrset", sel_headers(token), body)
        if code not in (200, 201):
            raise RuntimeError(f"Selectel POST A {name}: {code} {data}")
        report["steps"].append({"action": "created", "rr": f"A {name}", "to": ip})

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
