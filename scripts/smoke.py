#!/usr/bin/env python3
"""HTTP smoke tests against a running uncertainty API.

Covers valid covariance (PSD, including singular and zero-pivot cases) and
invalid covariance (dimension mismatch, asymmetry, non-PSD), plus a couple of
malformed-request cases. Uses only the standard library so it runs anywhere.

Exits 0 only if every assertion holds; the first failure of each case is
reported and counted.

Usage:
    python scripts/smoke.py [BASE_URL]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

_failures = 0
_checks = 0


def request(method: str, path: str, payload: Any = None) -> Tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        if isinstance(payload, (bytes, str)):
            data = payload.encode() if isinstance(payload, str) else payload
            headers["Content-Type"] = "application/json"
        else:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode()
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def check(name: str, condition: bool, detail: str = "") -> None:
    global _checks, _failures
    _checks += 1
    if condition:
        print(f"  PASS  {name}")
    else:
        _failures += 1
        print(f"  FAIL  {name}  {detail}")


def valid_case(limit: str = "5") -> Dict[str, Any]:
    return {
        "inputs": [
            {"id": "x1", "measurement": "1/2", "sensitivity": 2},
            {"id": "x2", "measurement": "3/2", "sensitivity": "1/3"},
        ],
        "covariance": [["4", "1"], ["1", "9"]],
        "intercept": "1/10",
        "variance_limit": limit,
    }


print(f"Smoke testing API at {BASE_URL}")

# 0. Health
status, body = request("GET", "/health")
check("health returns 200 ok", status == 200 and body == {"status": "ok"},
     f"status={status} body={body!r}")

# 1. Valid PSD covariance, within budget
status, body = request("POST", "/api/uncertainty/evaluate", valid_case("100"))
check("valid covariance -> 200", status == 200, f"status={status} body={body}")
check(
    "exact estimate 8/5",
    isinstance(body, dict) and body.get("estimate") == "8/5",
    repr(body),
)
check(
    "exact combined variance 55/3",
    isinstance(body, dict) and body.get("combined_variance") == "55/3",
    repr(body),
)
check(
    "within budget flagged over_limit=false",
    isinstance(body, dict) and body.get("over_limit") is False,
    repr(body),
)

# 2. Same covariance, tight budget -> over limit, no misleading release field
status, body = request("POST", "/api/uncertainty/evaluate", valid_case("18"))
check("tight budget -> 200", status == 200, repr(body))
check("over_limit=true", isinstance(body, dict) and body.get("over_limit") is True,
      repr(body))
check(
    "no misleading release fields",
    isinstance(body, dict) and "approved" not in body and "passed" not in body,
    repr(body),
)

# 3. Boundary equality is within budget (exact compare)
status, body = request("POST", "/api/uncertainty/evaluate", valid_case("55/3"))
check("exact equality is within budget", status == 200 and body.get("over_limit") is False,
      repr(body))

# 4. Singular PSD covariance (degenerate common calibration source)
singular = {
    "inputs": [
        {"id": "a", "measurement": 1, "sensitivity": 1},
        {"id": "b", "measurement": 1, "sensitivity": 1},
    ],
    "covariance": [["1", "1"], ["1", "1"]],
    "intercept": 0,
    "variance_limit": 4,
}
status, body = request("POST", "/api/uncertainty/evaluate", singular)
check("singular PSD covariance -> 200", status == 200, repr(body))
check("variance = 4", body.get("combined_variance") == "4", repr(body))

# 5. Invalid covariance: dimension mismatch
bad_dims = valid_case()
bad_dims["covariance"] = [["4", "1"]]
status, body = request("POST", "/api/uncertainty/evaluate", bad_dims)
check("dimension mismatch -> 422", status == 422, repr(body))
check(
    "stable code INVALID_MATRIX_DIMENSIONS with field",
    isinstance(body, dict)
    and body.get("error", {}).get("code") == "INVALID_MATRIX_DIMENSIONS"
    and body.get("error", {}).get("field") == "covariance",
    repr(body),
)

# 6. Invalid covariance: not exactly symmetric
asym = valid_case()
asym["covariance"] = [["4", "1"], ["2", "9"]]
status, body = request("POST", "/api/uncertainty/evaluate", asym)
check("asymmetric covariance -> 422", status == 422, repr(body))
check(
    "stable code MATRIX_NOT_SYMMETRIC with located cell",
    isinstance(body, dict)
    and body.get("error", {}).get("code") == "MATRIX_NOT_SYMMETRIC"
    and body.get("error", {}).get("field") == "covariance[0][1]",
    repr(body),
)

# 7. Invalid covariance: symmetric but indefinite (negative eigenvalue)
indef = valid_case()
indef["covariance"] = [["1", "2"], ["2", "1"]]
status, body = request("POST", "/api/uncertainty/evaluate", indef)
check("indefinite covariance -> 422", status == 422, repr(body))
check(
    "stable code MATRIX_NOT_POSITIVE_SEMIDEFINITE",
    isinstance(body, dict)
    and body.get("error", {}).get("code")
    == "MATRIX_NOT_POSITIVE_SEMIDEFINITE",
    repr(body),
)

# 8. Invalid covariance: zero-diagonal indefinite block
zero_indef = valid_case()
zero_indef["covariance"] = [["0", "1"], ["1", "0"]]
status, body = request("POST", "/api/uncertainty/evaluate", zero_indef)
check("zero-diagonal indefinite -> 422", status == 422, repr(body))
check(
    "reported as not PSD, no estimate leaked",
    isinstance(body, dict)
    and body.get("error", {}).get("code")
    == "MATRIX_NOT_POSITIVE_SEMIDEFINITE"
    and "estimate" not in body,
    repr(body),
)

# 9. Malformed JSON gets a stable code
status, body = request("POST", "/api/uncertainty/evaluate", "{oops")
check("malformed JSON -> 400 MALFORMED_JSON",
      status == 400 and isinstance(body, dict)
      and body.get("error", {}).get("code") == "MALFORMED_JSON",
      repr(body))

# 10. Negative variance budget
neg = valid_case("-1")
status, body = request("POST", "/api/uncertainty/evaluate", neg)
check("negative variance limit -> 422", status == 422, repr(body))
check(
    "stable code NEGATIVE_VARIANCE_LIMIT",
    isinstance(body, dict)
    and body.get("error", {}).get("code") == "NEGATIVE_VARIANCE_LIMIT",
    repr(body),
)

print(f"\n{_checks - _failures}/{_checks} smoke checks passed")
if _failures:
    print(f"{_failures} FAILURE(S)", file=sys.stderr)
    sys.exit(1)
print("SMOKE OK")
