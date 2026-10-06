"""HTTP layer for exact rational uncertainty evaluation.

Implements ``POST /api/uncertainty/evaluate`` and ``GET /health``.

All semantic validation returns a stable envelope::

    {"error": {"code": <STABLE_CODE>, "message": <str>, "field": <path>}}

so callers can locate the offending input and never receive a misleading
release decision for an invalid or out-of-budget budget.
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .rational import (
    MAX_INPUTS,
    MIN_INPUTS,
    format_fraction,
    is_positive_semidefinite,
    is_symmetric,
    parse_rational,
    propagate,
)

app = FastAPI(title="Exact Rational Uncertainty Evaluator", version="1.0.0")


class APIError(Exception):
    def __init__(self, code: str, message: str, field: str = "", status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.status = status


def _error_response(exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "field": exc.field,
            }
        },
    )


@app.exception_handler(APIError)
async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
    return _error_response(exc)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


def _require_object(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise APIError("INVALID_REQUEST", f"{field} must be an object", field, 422)
    return value


def _require_key(obj: Dict[str, Any], key: str) -> Any:
    if key not in obj:
        raise APIError("MISSING_FIELD", f"missing required field '{key}'", key, 422)
    return obj[key]


def _parse_inputs(raw: Any) -> List[Tuple[Any, Fraction, Fraction]]:
    """Validate the ordered input list.

    Returns ``[(id, measurement, sensitivity), ...]`` in request order.
    """
    if not isinstance(raw, list):
        raise APIError("INVALID_REQUEST", "'inputs' must be a list", "inputs", 422)
    if not (MIN_INPUTS <= len(raw) <= MAX_INPUTS):
        raise APIError(
            "INVALID_INPUT_COUNT",
            f"number of inputs must be between {MIN_INPUTS} and {MAX_INPUTS}",
            "inputs",
            422,
        )

    parsed: List[Tuple[Any, Fraction, Fraction]] = []
    seen_ids = set()
    for k, item in enumerate(raw):
        prefix = f"inputs[{k}]"
        obj = _require_object(item, prefix)

        if "id" not in obj:
            raise APIError(
                "MISSING_FIELD", "missing required field 'id'", f"{prefix}.id"
            )
        raw_id = obj["id"]
        if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
            raise APIError(
                "INVALID_INPUT_ID",
                "input id must be an integer or string",
                f"{prefix}.id",
            )
        if isinstance(raw_id, str) and not raw_id.strip():
            raise APIError(
                "INVALID_INPUT_ID", "input id must not be blank", f"{prefix}.id"
            )
        if raw_id in seen_ids:
            raise APIError(
                "DUPLICATE_INPUT_ID",
                f"duplicate input id {raw_id!r}; ids must be unique",
                f"{prefix}.id",
            )
        seen_ids.add(raw_id)

        if "measurement" not in obj:
            raise APIError(
                "MISSING_FIELD",
                "missing required field 'measurement'",
                f"{prefix}.measurement",
            )
        try:
            measurement = parse_rational(obj["measurement"])
        except ValueError as exc:
            raise APIError(
                "INVALID_RATIONAL", str(exc), f"{prefix}.measurement"
            ) from None

        if "sensitivity" not in obj:
            raise APIError(
                "MISSING_FIELD",
                "missing required field 'sensitivity'",
                f"{prefix}.sensitivity",
            )
        try:
            sensitivity = parse_rational(obj["sensitivity"])
        except ValueError as exc:
            raise APIError(
                "INVALID_RATIONAL", str(exc), f"{prefix}.sensitivity"
            ) from None

        parsed.append((raw_id, measurement, sensitivity))
    return parsed


def _parse_matrix(raw: Any, n: int) -> List[List[Fraction]]:
    field = "covariance"
    if not isinstance(raw, list):
        raise APIError("INVALID_MATRIX_DIMENSIONS", "'covariance' must be a list", field)
    if len(raw) != n:
        raise APIError(
            "INVALID_MATRIX_DIMENSIONS",
            f"covariance must be an {n}x{n} matrix matching the {n} input(s)",
            field,
        )

    matrix: List[List[Fraction]] = []
    for i, row in enumerate(raw):
        if not isinstance(row, list):
            raise APIError(
                "INVALID_MATRIX_DIMENSIONS",
                f"covariance row {i} must be a list",
                f"{field}[{i}]",
            )
        if len(row) != n:
            raise APIError(
                "INVALID_MATRIX_DIMENSIONS",
                f"covariance row {i} has length {len(row)}, expected {n}",
                f"{field}[{i}]",
            )
        parsed_row: List[Fraction] = []
        for j, cell in enumerate(row):
            try:
                parsed_row.append(parse_rational(cell))
            except ValueError as exc:
                raise APIError(
                    "INVALID_RATIONAL", str(exc), f"{field}[{i}][{j}]"
                ) from None
        matrix.append(parsed_row)
    return matrix


def _parse_scalar_rational(raw: Any, field: str) -> Fraction:
    try:
        return parse_rational(raw)
    except ValueError as exc:
        raise APIError("INVALID_RATIONAL", str(exc), field) from None


@app.post("/api/uncertainty/evaluate")
async def evaluate(request: Request) -> JSONResponse:
    # Parse the body as JSON ourselves so malformed JSON gets a stable code
    # instead of FastAPI's default 422 validation envelope.
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error_response(
            APIError("MALFORMED_JSON", "request body must be valid JSON", "", 400)
        )
    except Exception:
        return _error_response(
            APIError("MALFORMED_JSON", "request body could not be read", "", 400)
        )

    payload = _require_object(body, "$")

    inputs = _parse_inputs(_require_key(payload, "inputs"))
    n = len(inputs)
    covariance = _parse_matrix(_require_key(payload, "covariance"), n)
    intercept = _parse_scalar_rational(_require_key(payload, "intercept"), "intercept")
    variance_limit = _parse_scalar_rational(
        _require_key(payload, "variance_limit"), "variance_limit"
    )
    if variance_limit < 0:
        raise APIError(
            "NEGATIVE_VARIANCE_LIMIT",
            "variance_limit must be non-negative",
            "variance_limit",
        )

    # Exact structural matrix validation.
    if not is_symmetric(covariance, n):
        first = None
        for i in range(n):
            for j in range(i + 1, n):
                if covariance[i][j] != covariance[j][i]:
                    first = (i, j)
                    break
            if first:
                break
        i, j = first  # type: ignore[misc]
        raise APIError(
            "MATRIX_NOT_SYMMETRIC",
            f"covariance must be exactly symmetric: "
            f"[{i}][{j}] != [{j}][{i}]",
            f"covariance[{i}][{j}]",
        )

    if not is_positive_semidefinite(covariance, n):
        raise APIError(
            "MATRIX_NOT_POSITIVE_SEMIDEFINITE",
            "covariance matrix must be positive semidefinite",
            "covariance",
        )

    values = [measurement for _, measurement, _ in inputs]
    sensitivities = [sensitivity for _, _, sensitivity in inputs]
    estimate, combined_variance = propagate(
        values, sensitivities, covariance, intercept
    )

    over_limit = combined_variance > variance_limit

    return JSONResponse(
        status_code=200,
        content={
            "estimate": format_fraction(estimate),
            "combined_variance": format_fraction(combined_variance),
            "over_limit": over_limit,
        },
    )
