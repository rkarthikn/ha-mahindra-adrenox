"""The HAR redaction helper owners run before sharing a capture."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "redact_har", Path(__file__).parents[1] / "tools" / "redact_har.py"
)
redact_har = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redact_har)

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlLXNpZ25hdHVyZQ"
SECRETS = (JWT, "9876543210", "MA1TEST0000000001", "owner@example.com", "12.9716", "Ravi", "424242")


def test_redacts_secrets_and_keeps_shape() -> None:
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": "https://api.example.test/v1/vehicles/MA1TEST0000000001/status?mobile=9876543210",
                        "headers": [
                            {"name": "Authorization", "value": f"Bearer {JWT}"},
                            {"name": "app-version", "value": "4.2.0"},
                        ],
                        "postData": {
                            "text": json.dumps({"otp": "424242", "note": "call 9876543210"})
                        },
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Set-Cookie", "value": "sid=abc"}],
                        "content": {
                            "text": json.dumps(
                                {
                                    "data": {
                                        "fuelLevel": 62,
                                        "lockStatus": "LOCKED",
                                        "customerName": "Ravi",
                                        "email": "owner@example.com",
                                        "location": {"latitude": 12.9716, "longitude": 77.59},
                                        "trips": [1, 2, 3, 4, 5],
                                    }
                                }
                            )
                        },
                    },
                }
            ]
        }
    }
    [entry] = redact_har.summarize(har)
    text = json.dumps(entry)
    for secret in SECRETS:
        assert secret not in text, secret
    assert entry["path"] == "/v1/vehicles/<VIN>/status"
    assert entry["request_headers"] == {"Authorization": "<redacted>", "app-version": "4.2.0"}
    assert entry["request_body"] == {"otp": "<str>", "note": "call <MOBILE>"}
    data = entry["response_body"]["data"]
    assert data["fuelLevel"] == 62
    assert data["lockStatus"] == "LOCKED"
    assert data["location"] == {"latitude": "<float>", "longitude": "<float>"}
    assert data["trips"] == [1, 2, 3, "<...>"]
    assert entry["response_headers"] == ["Set-Cookie"]
