# Exact Rational Uncertainty Evaluator

对多传感器测量结果做**精确有理数**的线性不确定度传播与方差预算核对。
所有数值（测量值、灵敏度、协方差、截距、方差上限）均以
`fractions.Fraction` 精确计算，**不使用任何浮点近似**，因此“是否超限”的
裁决不会被舍入误差或浮点容差掩盖。

## 评定模型

对按序排列、编号唯一的输入量 \(x_i\)（1 ≤ n ≤ 24）：

- 估计值：`estimate = intercept + Σ sensitivity_i · measurement_i`
- 合成方差：`combined_variance = Σ_i Σ_j sensitivity_i · C_ij · sensitivity_j`
- 判定：`over_limit = combined_variance > variance_limit`（相等视为**未超限**）

矩阵 `C` 必须与输入数量维度匹配、**精确对称**且**半正定（PSD）**。
PSD 由带对称选主元的 LDLᵀ 分解精确判定（能正确处理零前导行/列，
如 `[[0,0],[0,1]]`），与浮点特征值无涉。

## 有理数格式

- 整数：`123`、`-7`、`0`
- 最简分数：`"1/2"`、`"-3/4"`（分母必须为正，且必须已约分；`"2/4"`、`"5/1"` 会被拒绝）
- **不接受** JSON 浮点数（如 `0.5`），从源头杜绝不精确输入

## API

### `GET /health`

返回 `200 {"status":"ok"}`，表示应用已能接收请求（用于容器健康检查）。

### `POST /api/uncertainty/evaluate`

请求：

```json
{
  "inputs": [
    {"id": "x1", "measurement": "1/2", "sensitivity": 2},
    {"id": "x2", "measurement": "3/2", "sensitivity": "1/3"}
  ],
  "covariance": [["4", "1"], ["1", "9"]],
  "intercept": "1/10",
  "variance_limit": "100"
}
```

成功响应 `200`（均为最简有理数，整数不带 `/1`）：

```json
{
  "estimate": "8/5",
  "combined_variance": "55/3",
  "over_limit": false
}
```

超限时 `over_limit` 为 `true`；响应中不存在任何 `approved`/`passed` 等
可能被误读为“放行”的字段。

错误响应统一为 `{"error":{"code","message","field"}}`，状态码稳定：

| code | HTTP | 含义 / 可定位字段示例 |
|---|---|---|
| `MALFORMED_JSON` | 400 | 请求体不是合法 JSON |
| `INVALID_REQUEST` | 422 | `inputs` 非数组、请求体非对象（`field: "$"`） |
| `MISSING_FIELD` | 422 | 缺少字段，如 `field: "intercept"` |
| `INVALID_INPUT_COUNT` | 422 | 输入数量不在 1–24，`field: "inputs"` |
| `INVALID_INPUT_ID` | 422 | `field: "inputs[k].id"` |
| `DUPLICATE_INPUT_ID` | 422 | 编号重复，`field: "inputs[k].id"` |
| `INVALID_RATIONAL` | 422 | 非整数/非最简/分母非正/使用浮点，字段定位到具体单元格 |
| `INVALID_MATRIX_DIMENSIONS` | 422 | 维度不匹配，定位到 `covariance` 或 `covariance[i]` |
| `MATRIX_NOT_SYMMETRIC` | 422 | 非精确对称，定位首个不一致单元格 `covariance[i][j]` |
| `MATRIX_NOT_POSITIVE_SEMIDEFINITE` | 422 | 非半正定，`field: "covariance"` |
| `NEGATIVE_VARIANCE_LIMIT` | 422 | 方差上限为负，`field: "variance_limit"` |

矩阵不合法或预算无效时不会返回任何估计值/放行结论。

## 运行（Docker Compose）

宿主机端口由环境变量 `API_HOST_PORT` 配置（默认 8000）：

```bash
API_HOST_PORT=9090 docker compose up --build
# 健康检查
curl -s http://localhost:9090/health
```

### 一次性验证服务 `verify`

`verify` 会等待 `api` 健康检查通过，然后依次执行：

1. 单元测试与 HTTP 测试（pytest）
2. 应用构建检查（`compileall` 字节码编译 + 应用导入）
3. 有效与无效协方差的 HTTP 冒烟（`scripts/smoke.py`）

任一步失败即以非零退出码退出，全部通过则自行退出：

```bash
docker compose build
docker compose run --rm verify
```

## 本地开发

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
uvicorn app.main:app --reload
python scripts/smoke.py http://127.0.0.1:8000
```

## 目录

```
app/rational.py   # 精确有理运算、精确对称/PSD 判定、协方差传播
app/main.py       # FastAPI 路由与字段级校验
tests/            # pytest 单元 + HTTP 测试（76 用例）
scripts/smoke.py  # 仅依赖标准库的 HTTP 冒烟脚本
Dockerfile        # 非 root 运行，内置 /health 健康检查
docker-compose.yml# api（可配置宿主端口）+ 一次性 verify 服务
```
