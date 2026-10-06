"""HTTP-level tests for POST /api/uncertainty/evaluate and /health."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def valid_payload(**overrides):
    payload = {
        "inputs": [
            {"id": "x1", "measurement": "1/2", "sensitivity": 2},
            {"id": 7, "measurement": "3/2", "sensitivity": "1/3"},
        ],
        "covariance": [
            ["4", "1"],
            ["1", "9"],
        ],
        "intercept": "1/10",
        "variance_limit": "100",
    }
    payload.update(overrides)
    return payload


def post(payload):
    return client.post("/api/uncertainty/evaluate", json=payload)


class TestHealth:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestSuccess:
    def test_exact_result_within_budget(self):
        resp = post(valid_payload())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # estimate = 1/10 + 2*(1/2) + (1/3)*(3/2) = 1/10 + 1 + 1/2 = 8/5
        assert body["estimate"] == "8/5"
        # variance = 4*2^2 + 2*1*2*(1/3) + 9*(1/3)^2 = 16 + 4/3 + 1 = 55/3
        assert body["combined_variance"] == "55/3"
        assert body["over_limit"] is False

    def test_over_limit_flagged_not_rounded(self):
        # 55/3 = 18.333...; limit 18 must be over, limit 56/3 must be within
        resp = post(valid_payload(variance_limit="18"))
        assert resp.json()["over_limit"] is True

        resp = post(valid_payload(variance_limit="56/3"))
        body = resp.json()
        assert body["over_limit"] is False
        assert body["combined_variance"] == "55/3"

    def test_boundary_equal_is_within_budget(self):
        resp = post(valid_payload(variance_limit="55/3"))
        body = resp.json()
        assert resp.status_code == 200
        assert body["over_limit"] is False

    def test_integer_outputs_serialized_without_slash(self):
        payload = {
            "inputs": [{"id": "a", "measurement": 2, "sensitivity": 3}],
            "covariance": [["1"]],
            "intercept": 0,
            "variance_limit": 9,
        }
        body = post(payload).json()
        assert body["estimate"] == "6"
        assert body["combined_variance"] == "9"
        assert body["over_limit"] is False

    def test_24_inputs_accepted(self):
        n = 24
        payload = {
            "inputs": [
                {"id": f"i{k}", "measurement": 0, "sensitivity": 1}
                for k in range(n)
            ],
            "covariance": [
                [1 if i == j else 0 for j in range(n)] for i in range(n)
            ],
            "intercept": 0,
            "variance_limit": n,
        }
        resp = post(payload)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "estimate": "0",
            "combined_variance": "24",
            "over_limit": False,
        }

    def test_singular_psd_matrix_accepted(self):
        payload = {
            "inputs": [
                {"id": "a", "measurement": 1, "sensitivity": 1},
                {"id": "b", "measurement": 1, "sensitivity": 1},
            ],
            "covariance": [["1", "1"], ["1", "1"]],
            "intercept": 0,
            "variance_limit": "4",
        }
        resp = post(payload)
        assert resp.status_code == 200, resp.text
        assert resp.json()["combined_variance"] == "4"


class TestMalformedRequest:
    def test_malformed_json(self):
        resp = client.post(
            "/api/uncertainty/evaluate",
            content="{not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "MALFORMED_JSON"

    def test_body_not_object(self):
        resp = client.post("/api/uncertainty/evaluate", json=[1, 2, 3])
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    def test_missing_field(self):
        payload = valid_payload()
        del payload["intercept"]
        resp = post(payload)
        assert resp.status_code == 422
        err = resp.json()["error"]
        assert err["code"] == "MISSING_FIELD"
        assert err["field"] == "intercept"

    def test_missing_nested_field_is_located(self):
        payload = valid_payload()
        del payload["inputs"][0]["measurement"]
        err = post(payload).json()["error"]
        assert err["code"] == "MISSING_FIELD"
        assert err["field"] == "inputs[0].measurement"

    def test_missing_id_is_located(self):
        payload = valid_payload()
        del payload["inputs"][1]["id"]
        err = post(payload).json()["error"]
        assert err["code"] == "MISSING_FIELD"
        assert err["field"] == "inputs[1].id"

    def test_input_item_not_object(self):
        payload = valid_payload()
        payload["inputs"][0] = 42
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_REQUEST"
        assert err["field"] == "inputs[0]"


class TestInputValidation:
    def test_too_few_inputs(self):
        payload = valid_payload(inputs=[])
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_INPUT_COUNT"
        assert err["field"] == "inputs"

    def test_too_many_inputs(self):
        n = 25
        payload = {
            "inputs": [
                {"id": k, "measurement": 0, "sensitivity": 0} for k in range(n)
            ],
            "covariance": [[0] * n for _ in range(n)],
            "intercept": 0,
            "variance_limit": 0,
        }
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_INPUT_COUNT"

    def test_duplicate_ids(self):
        payload = valid_payload()
        payload["inputs"][1]["id"] = "x1"
        err = post(payload).json()["error"]
        assert err["code"] == "DUPLICATE_INPUT_ID"
        assert err["field"] == "inputs[1].id"

    def test_boolean_id_rejected(self):
        payload = valid_payload()
        payload["inputs"][0]["id"] = True
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_INPUT_ID"

    def test_invalid_measurement_float(self):
        payload = valid_payload()
        payload["inputs"][0]["measurement"] = 0.5
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_RATIONAL"
        assert err["field"] == "inputs[0].measurement"

    def test_unreduced_rational_rejected(self):
        payload = valid_payload()
        payload["inputs"][0]["measurement"] = "2/4"
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_RATIONAL"
        assert err["field"] == "inputs[0].measurement"

    def test_negative_denominator_rejected(self):
        payload = valid_payload()
        payload["inputs"][1]["sensitivity"] = "1/-3"
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_RATIONAL"
        assert err["field"] == "inputs[1].sensitivity"


class TestMatrixValidation:
    def test_wrong_row_count(self):
        payload = valid_payload(covariance=[["4", "1"]])
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_MATRIX_DIMENSIONS"
        assert err["field"] == "covariance"

    def test_wrong_row_width(self):
        payload = valid_payload(covariance=[["4", "1"], ["1"]])
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_MATRIX_DIMENSIONS"
        assert err["field"] == "covariance[1]"

    def test_not_symmetric_field_located(self):
        payload = valid_payload(covariance=[["4", "1"], ["2", "9"]])
        err = post(payload).json()["error"]
        assert err["code"] == "MATRIX_NOT_SYMMETRIC"
        assert err["field"] == "covariance[0][1]"

    def test_indefinite_with_zero_diagonal(self):
        payload = valid_payload(covariance=[["0", "1"], ["1", "0"]])
        err = post(payload).json()["error"]
        assert err["code"] == "MATRIX_NOT_POSITIVE_SEMIDEFINITE"
        assert err["field"] == "covariance"

    def test_indefinite_negative_eigenvalue(self):
        payload = valid_payload(covariance=[["1", "2"], ["2", "1"]])
        err = post(payload).json()["error"]
        assert err["code"] == "MATRIX_NOT_POSITIVE_SEMIDEFINITE"

    def test_zero_leading_row_psd_accepted(self):
        # [[0,0],[0,1]] is PSD but defeats unpivoted LDL; must pass here.
        payload = {
            "inputs": [
                {"id": "a", "measurement": 0, "sensitivity": 0},
                {"id": "b", "measurement": 0, "sensitivity": 1},
            ],
            "covariance": [["0", "0"], ["0", "1"]],
            "intercept": 0,
            "variance_limit": 1,
        }
        resp = post(payload)
        assert resp.status_code == 200, resp.text
        assert resp.json()["combined_variance"] == "1"

    def test_invalid_cell_rational(self):
        payload = valid_payload(covariance=[["4", "x"], ["x", "9"]])
        err = post(payload).json()["error"]
        assert err["code"] == "INVALID_RATIONAL"
        assert err["field"] == "covariance[0][1]"


class TestBudget:
    def test_negative_limit_rejected(self):
        err = post(valid_payload(variance_limit="-1/10")).json()["error"]
        assert err["code"] == "NEGATIVE_VARIANCE_LIMIT"
        assert err["field"] == "variance_limit"

    def test_negative_limit_integer(self):
        err = post(valid_payload(variance_limit=-1)).json()["error"]
        assert err["code"] == "NEGATIVE_VARIANCE_LIMIT"

    def test_invalid_limit_rational(self):
        err = post(valid_payload(variance_limit="1.5")).json()["error"]
        assert err["code"] == "INVALID_RATIONAL"
        assert err["field"] == "variance_limit"

    def test_invalid_intercept(self):
        err = post(valid_payload(intercept="oops")).json()["error"]
        assert err["code"] == "INVALID_RATIONAL"
        assert err["field"] == "intercept"

    def test_over_limit_response_is_never_a_release(self):
        # Over-budget responses must carry no positive release assertion:
        # over_limit is true and there is no "approved"/"passed" field.
        body = post(valid_payload(variance_limit=0)).json()
        assert body["over_limit"] is True
        assert "approved" not in body
        assert "passed" not in body
