#!/usr/bin/env python3
"""Minimal SigV4 S3 client for the IdP off-box backup chain.

Stdlib only: this runs from the daily systemd unit on the IdP host, and that
host deliberately has no pip payload. Path-style addressing, because Selectel
Object Storage serves buckets as /{bucket}/{key} on the pool endpoint.

Credentials come from /etc/keycloak/s3.env (root 600) via the environment:
S3_ENDPOINT, S3_REGION, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_PREFIX.

  s3.py put <file> [--key KEY]      upload, then HEAD it back and compare size
  s3.py get <key> <file>            download
  s3.py head <key>                  size + etag, exit 1 when absent
  s3.py list [--prefix P]           keys, newest last
  s3.py make-bucket                 idempotent
  s3.py set-lifecycle --days N      off-box retention (expire objects)
  s3.py get-lifecycle               read retention back
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class S3Error(RuntimeError):
    pass


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise S3Error(f"{name} is not set (expected in /etc/keycloak/s3.env)")
    return value


class S3:
    def __init__(self) -> None:
        self.endpoint = _env("S3_ENDPOINT").rstrip("/")
        self.region = _env("S3_REGION", "ru-1")
        self.bucket = _env("S3_BUCKET")
        self.access = _env("S3_ACCESS_KEY")
        self.secret = _env("S3_SECRET_KEY")
        self.prefix = os.environ.get("S3_PREFIX", "auth-qa-guru/").lstrip("/")
        self.host = urllib.parse.urlsplit(self.endpoint).netloc
        self.ctx = ssl.create_default_context()

    def key_for(self, name: str) -> str:
        return f"{self.prefix}{name}" if self.prefix else name

    def _sign(self, key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: bytes = b"",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        # Canonical URI keeps "/" literal; every other reserved char is encoded.
        canonical_uri = urllib.parse.quote(path, safe="/~")
        query = query or {}
        canonical_query = "&".join(
            f"{urllib.parse.quote(k, safe='~')}={urllib.parse.quote(v, safe='~')}"
            for k, v in sorted(query.items())
        )
        payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        now = dt.datetime.now(dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")

        headers = {
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        for k, v in (extra_headers or {}).items():
            headers[k.lower()] = v

        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical_request = (
            f"{method}\n{canonical_uri}\n{canonical_query}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        string_to_sign = (
            "AWS4-HMAC-SHA256\n"
            f"{amz_date}\n{scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )
        k = self._sign(("AWS4" + self.secret).encode(), datestamp)
        k = self._sign(k, self.region)
        k = self._sign(k, "s3")
        k = self._sign(k, "aws4_request")
        signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()

        url = f"{self.endpoint}{canonical_uri}"
        if canonical_query:
            url = f"{url}?{canonical_query}"
        req_headers = dict(headers)
        req_headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        req = urllib.request.Request(
            url, data=body or None, method=method, headers=req_headers
        )
        try:
            with urllib.request.urlopen(req, timeout=180, context=self.ctx) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()

    # --- operations -------------------------------------------------------
    def make_bucket(self) -> str:
        code, _, body = self._request("PUT", f"/{self.bucket}")
        if code in (200, 409):
            return "exists" if code == 409 else "created"
        raise S3Error(f"make-bucket {code}: {body[:300]!r}")

    def put(self, local: Path, key: str) -> dict[str, str]:
        payload = local.read_bytes()
        code, _, body = self._request(
            "PUT",
            f"/{self.bucket}/{key}",
            body=payload,
            extra_headers={"content-type": "application/octet-stream"},
        )
        if code not in (200, 201):
            raise S3Error(f"PUT {key} -> {code}: {body[:300]!r}")
        # A 200 is not proof. Read the object back and compare length, so a
        # truncated or silently-dropped upload fails the unit instead of
        # leaving a backup that only exists in the log line.
        meta = self.head(key)
        if int(meta["size"]) != len(payload):
            raise S3Error(
                f"{key} landed with {meta['size']} bytes, expected {len(payload)}"
            )
        return {"key": key, "size": meta["size"], "etag": meta["etag"]}

    def head(self, key: str) -> dict[str, str]:
        code, headers, _ = self._request("HEAD", f"/{self.bucket}/{key}")
        if code != 200:
            raise S3Error(f"HEAD {key} -> {code}")
        return {
            "size": headers.get("Content-Length", "0"),
            "etag": (headers.get("ETag") or "").strip('"'),
        }

    def get(self, key: str, dest: Path) -> int:
        code, _, body = self._request("GET", f"/{self.bucket}/{key}")
        if code != 200:
            raise S3Error(f"GET {key} -> {code}: {body[:200]!r}")
        dest.write_bytes(body)
        return len(body)

    def list(self, prefix: str | None = None) -> list[dict[str, str]]:
        query = {"list-type": "2", "prefix": prefix if prefix is not None else self.prefix}
        code, _, body = self._request("GET", f"/{self.bucket}", query=query)
        if code != 200:
            raise S3Error(f"LIST -> {code}: {body[:300]!r}")
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        out = [
            {
                "key": (node.findtext(f"{ns}Key") or ""),
                "size": (node.findtext(f"{ns}Size") or "0"),
                "modified": (node.findtext(f"{ns}LastModified") or ""),
            }
            for node in ET.fromstring(body).findall(f"{ns}Contents")
        ]
        return sorted(out, key=lambda o: o["modified"])

    def set_lifecycle(self, days: int) -> None:
        rule = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<LifecycleConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            "<Rule><ID>idp-dump-retention</ID>"
            f"<Filter><Prefix>{self.prefix}</Prefix></Filter>"
            "<Status>Enabled</Status>"
            f"<Expiration><Days>{days}</Days></Expiration>"
            "</Rule></LifecycleConfiguration>"
        ).encode()
        code, _, body = self._request(
            "PUT",
            f"/{self.bucket}",
            query={"lifecycle": ""},
            body=rule,
            extra_headers={
                "content-md5": base64.b64encode(hashlib.md5(rule).digest()).decode(),
                "content-type": "application/xml",
            },
        )
        if code not in (200, 204):
            raise S3Error(f"PUT lifecycle -> {code}: {body[:300]!r}")

    def get_lifecycle(self) -> str:
        code, _, body = self._request("GET", f"/{self.bucket}", query={"lifecycle": ""})
        if code != 200:
            raise S3Error(f"GET lifecycle -> {code}: {body[:200]!r}")
        return body.decode(errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_put = sub.add_parser("put")
    p_put.add_argument("file", type=Path)
    p_put.add_argument("--key")
    p_get = sub.add_parser("get")
    p_get.add_argument("key")
    p_get.add_argument("file", type=Path)
    p_head = sub.add_parser("head")
    p_head.add_argument("key")
    p_list = sub.add_parser("list")
    p_list.add_argument("--prefix")
    sub.add_parser("make-bucket")
    p_life = sub.add_parser("set-lifecycle")
    p_life.add_argument("--days", type=int, required=True)
    sub.add_parser("get-lifecycle")

    args = parser.parse_args(argv)
    try:
        s3 = S3()
        if args.cmd == "make-bucket":
            print(s3.make_bucket())
        elif args.cmd == "put":
            key = args.key or s3.key_for(args.file.name)
            meta = s3.put(args.file, key)
            print(f"uploaded s3://{s3.bucket}/{meta['key']} {meta['size']} bytes verified")
        elif args.cmd == "get":
            print(s3.get(args.key, args.file))
        elif args.cmd == "head":
            meta = s3.head(args.key)
            print(f"{meta['size']} {meta['etag']}")
        elif args.cmd == "list":
            for obj in s3.list(args.prefix):
                print(f"{obj['modified']}  {obj['size']:>10}  {obj['key']}")
        elif args.cmd == "set-lifecycle":
            s3.set_lifecycle(args.days)
            print(f"lifecycle: expire after {args.days} days")
        elif args.cmd == "get-lifecycle":
            print(s3.get_lifecycle())
    except S3Error as exc:
        print(f"s3: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
