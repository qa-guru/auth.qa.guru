#!/bin/sh
# Daily Keycloak pg_dump. Runs as root via systemd. Dump is local + optional S3.
# Hetzner Object Storage keys from the 2026-07 audit were revoked (InvalidAccessKeyId);
# drop /etc/keycloak/s3.env (root 600) when a working bucket exists.
set -euo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DIR=/var/backups/keycloak
DUMP="${DIR}/keycloak-${STAMP}.dump"

install -d -m 750 -o postgres -g postgres "${DIR}"
sudo -u postgres pg_dump -Fc -d keycloak -f "${DUMP}"
chmod 640 "${DUMP}"
chown postgres:postgres "${DUMP}"
find "${DIR}" -name 'keycloak-*.dump' -mtime +14 -delete

if [ -f /etc/keycloak/s3.env ]; then
  # shellcheck disable=SC1091
  set -a
  . /etc/keycloak/s3.env
  set +a
  DUMP="${DUMP}" python3 - <<'PY'
import datetime, hashlib, hmac, os, ssl, sys, urllib.request, urllib.error
from pathlib import Path

dump = Path(os.environ["DUMP"])
access = os.environ["S3_ACCESS_KEY"]
secret = os.environ["S3_SECRET_KEY"]
region = os.environ.get("S3_REGION", "fsn1")
endpoint = os.environ["S3_ENDPOINT"].rstrip("/")
bucket = os.environ["S3_BUCKET"]
key_name = f"auth-qa-guru/{dump.name}"
host = endpoint.split("://", 1)[-1]
payload = dump.read_bytes()
payload_hash = hashlib.sha256(payload).hexdigest()
now = datetime.datetime.now(datetime.timezone.utc)
amz = now.strftime("%Y%m%dT%H%M%SZ")
datestamp = now.strftime("%Y%m%d")
canonical_uri = f"/{bucket}/{key_name}"
canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz}\n"
signed_headers = "host;x-amz-content-sha256;x-amz-date"
canonical_request = f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
cr_hash = hashlib.sha256(canonical_request.encode()).hexdigest()
scope = f"{datestamp}/{region}/s3/aws4_request"
string_to_sign = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{cr_hash}"

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()

k = _sign(("AWS4" + secret).encode(), datestamp)
k = hmac.new(k, region.encode(), hashlib.sha256).digest()
k = hmac.new(k, b"s3", hashlib.sha256).digest()
k = hmac.new(k, b"aws4_request", hashlib.sha256).digest()
sig = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
auth = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed_headers}, Signature={sig}"
req = urllib.request.Request(
    f"{endpoint}{canonical_uri}",
    data=payload,
    method="PUT",
    headers={
        "Host": host,
        "x-amz-date": amz,
        "x-amz-content-sha256": payload_hash,
        "Authorization": auth,
        "Content-Type": "application/octet-stream",
    },
)
try:
    urllib.request.urlopen(req, timeout=120, context=ssl.create_default_context())
except urllib.error.HTTPError as exc:
    sys.stderr.write(f"S3 PUT failed {exc.code} {exc.read()[:400]}\n")
    sys.exit(1)
print(f"uploaded s3://{bucket}/{key_name}")
PY
fi

echo "dumped ${DUMP}"
