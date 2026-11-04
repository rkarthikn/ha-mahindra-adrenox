#!/usr/bin/env python3
"""Shrink a HAR capture of the AdrenoX app to a redacted endpoint summary.

    python3 tools/redact_har.py capture.har > adrenox-endpoints.json

Keeps: method, host, path, status, header *names*, JSON field names, value
types and plain numbers/states. Replaces: tokens, cookies, auth headers, JWTs,
phone numbers, emails, VIN-like strings, GPS coordinates, OTPs, names and
anything in a field whose name looks personal.

It is a safety net, not a guarantee. Read the output before sharing it.
Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any
from urllib.parse import parse_qsl, urlsplit

# Header values worth keeping because they are the same for every user.
KEEP_HEADER_VALUES = {
    "accept",
    "content-type",
    "user-agent",
    "app-version",
    "x-app-version",
    "platform",
    "x-platform",
    "os",
    "x-os",
    "accept-language",
}

SENSITIVE_KEY = re.compile(
    r"token|secret|password|passwd|pin|otp|auth|cookie|session|signature|"
    r"mobile|phone|msisdn|email|name|address|vin|chassis|engine|imei|iccid|"
    r"reg(istration)?(no|number)?$|vehicleNumber|lat|lng|lon|customer|user_?id|dob|aadhaar|pan",
    re.IGNORECASE,
)
# Field names that match the pattern above but are harmless and useful.
SAFE_KEY = re.compile(r"^(modelName|vehicleName|variant|fuel.*|.*Status|.*Level)$", re.IGNORECASE)

JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*")
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
LONG_SECRET = re.compile(r"\b[A-Za-z0-9_\-+/=]{32,}\b")


def scrub_text(text: str) -> str:
    text = JWT.sub("<JWT>", text)
    text = EMAIL.sub("<EMAIL>", text)
    text = VIN.sub("<VIN>", text)
    text = PHONE.sub("<MOBILE>", text)
    return LONG_SECRET.sub("<SECRET>", text)


def scrub(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, key) for v in value[:3]] + (["<...>"] if len(value) > 3 else [])
    if key and SENSITIVE_KEY.search(key) and not SAFE_KEY.match(key):
        if value is None or isinstance(value, bool):
            return value
        return f"<{type(value).__name__}>"
    if isinstance(value, str):
        return scrub_text(value)
    return value


def body(text: str | None) -> Any:
    if not text:
        return None
    try:
        return scrub(json.loads(text))
    except ValueError:
        return f"<non-JSON, {len(text)} chars>"


def summarize(har: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for entry in har.get("log", {}).get("entries", []):
        req, resp = entry["request"], entry["response"]
        url = urlsplit(req["url"])
        out.append(
            {
                "method": req["method"],
                "host": url.hostname,
                "path": scrub_text(url.path),
                "query": {k: scrub(v, k) for k, v in parse_qsl(url.query)},
                "request_headers": {
                    h["name"]: h["value"]
                    if h["name"].lower() in KEEP_HEADER_VALUES
                    else "<redacted>"
                    for h in req.get("headers", [])
                },
                "request_body": body((req.get("postData") or {}).get("text")),
                "status": resp.get("status"),
                "response_headers": sorted(h["name"] for h in resp.get("headers", [])),
                "response_body": body((resp.get("content") or {}).get("text")),
            }
        )
    return out


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as fh:
        har = json.load(fh)
    json.dump(summarize(har), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
