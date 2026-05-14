# GPU Arbiter

GPU Arbiter 是一個給單機 Docker Compose AI 堆疊使用的輕量級反向代理。
它透過全域 in-memory lock 序列化 GPU 任務、在 unload hooks 執行後輪詢 VRAM 可用量、
等待服務就緒，最後將請求轉發給 upstream 模型服務。

目標：單張 3090/4090 等級主機上同時跑異質服務（圖像、語音、音樂、LLM chat）。
不是 LiteLLM、Triton、KServe 或 GPUStack 的替代品。

## 語言

- 繁體中文：這份檔案
- English: [README.en.md](./README.en.md)

---

## 請求流程（GPU 路由）

對每一條已設定的 GPU route，GPU Arbiter 依序執行：

1. **解析模型** — 從 request body 的 `model` 欄位或路由路徑判定目標模型。
2. **取得 GPU lock** — in-memory；並行請求在此排隊。
3. **執行 `unload` hooks** — 呼叫其他服務釋放 VRAM。Best-effort（錯誤忽略）。
4. **輪詢 VRAM** — 每 2 秒透過 NVML 讀取可用 GPU 記憶體，最多等 60 秒。逾時回傳 `503`。
5. **執行 `health` hook** — 等待 upstream 就緒（每個模型可選）。
6. **轉發請求** — 將原始請求傳給 upstream 服務，同時持續偵測 client 是否斷線。
   - 若 `max_proxy_seconds` 達到上限，回傳 `504` 並釋放 lock。
   - 若 client 在 proxy 期間斷線，立即取消請求，回傳 `499` 並釋放 lock。
7. **Cooldown** — 可選的等待時間，釋放 lock 前先睡眠（避免連打）。
8. **執行 `cleanup` hooks** — 在 GPU lock **釋放後**才執行，呼叫服務卸載 VRAM（不阻塞後續請求排隊）。

非 GPU 路由（`uses_gpu: false`）跳過步驟 2–5，直接轉發。

---

## API 端點

### `GET /health`

回傳 arbiter 狀態與目前 GPU 狀態。

```json
{
  "status": "ok",
  "gpu": { "index": 0, "free_mb": 22000 },
  "models": ["local/image-turbo", "local/tts"],
  "holder": null
}
```

`holder` 是目前持有 GPU lock 的模型 ID，閒置時為 `null`。

---

### `GET /models`

回傳所有已設定的模型 ID 列表。

```json
{ "data": [{ "id": "local/image-turbo" }, { "id": "local/tts" }] }
```

---

### `POST /admin/unload`

對所有模型執行全部 `unload` hooks。適合在維護前或切換大型模型前清空 GPU。

**成功：**
```json
{ "status": "ok" }
```

**GPU 忙碌（409）：**
```json
{
  "error": {
    "type": "gpu_busy",
    "message": "GPU is occupied by another generation job",
    "retryable": true,
    "holder": "local/image-turbo"
  }
}
```

---

### `POST /queue` — 非同步任務提交（多租戶）

不阻塞地送出 GPU 任務。任務按 FIFO 排隊，跨租戶以 round-robin 公平排程。
需要 `X-Tenant-ID` header，每個租戶最多 10 筆 pending 任務。

**Request：**
```
POST /queue
X-Tenant-ID: my-org
Content-Type: application/json

{ "model": "local/image-turbo", "prompt": "..." }
```

**Response (202)：**
```json
{ "task_id": "a3f8c1...", "status": "pending" }
```

**錯誤：** `400` 缺少租戶、`404` 未知模型、`429` 佇列已滿。

---

### `GET /tasks` — 列出租戶任務

回傳呼叫者的所有任務，可用 `?status=` 篩選。

```
GET /tasks?status=pending
X-Tenant-ID: my-org
```

**Response (200)：**
```json
{
  "tasks": [
    { "task_id": "a3f8c1...", "status": "pending", "model_id": "local/chat", "created_at": 1234.5 }
  ]
}
```

狀態值：`pending`、`running`、`done`、`failed`、`cancelled`。

---

### `GET /tasks/{task_id}` — 輪詢任務狀態

輪詢直到 `status` 為 `done`、`failed` 或 `cancelled`。若 task 屬於其他租戶回傳 `404`。

**Response (200)：**
```json
{
  "task_id": "a3f8c1...",
  "status": "done",
  "result": {
    "status_code": 200,
    "body": "...",
    "headers": { "content-type": "application/json" },
    "error": null
  }
}
```

`status` 為 `pending` 或 `running` 時 `result` 為 `null`。

---

### `DELETE /tasks/{task_id}` — 取消任務

取消 `pending` 任務。任務已是 `running`/`done`/`failed`/`cancelled` 時回傳 `409`。

**Response (200)：**
```json
{ "task_id": "a3f8c1...", "status": "cancelled" }
```

---

### `GET /queue/status` — 佇列概況

```json
{ "pending": 3, "running": 1, "tenants": ["org-a", "org-b"] }
```

---

### `POST|GET /<任意路徑>` — 模型代理（同步）

所有其他路徑會被路由到對應模型的 upstream。模型解析順序：
1. Request body JSON 中的 `"model"` 欄位（如 `{"model": "local/image-turbo", ...}`）
2. 路由路徑本身（當只有一個模型匹配該路徑時）

**範例 — 圖像生成：**
```
POST /v1/images/generations
Authorization: Bearer <token>
Content-Type: application/json

{ "model": "local/image-turbo", "prompt": "...", ... }
```

**成功：** upstream 的回應原封不動轉發（status + body + headers）。

---

## 錯誤回應

所有錯誤使用相同結構：

```json
{
  "error": {
    "type": "<錯誤類型>",
    "message": "<說明>",
    "retryable": true | false,
    ...額外欄位...
  }
}
```

| HTTP | `type` | 意義 | 可重試 |
|------|--------|------|--------|
| 409 | `gpu_busy` | 其他請求持有 GPU lock | ✅ |
| 503 | `insufficient_vram` | 輪詢 60 秒後 VRAM 仍不足 | ✅ |
| 404 | `model_not_found` | 沒有模型匹配此路由或 `model` 欄位 | ❌ |
| 502 | `upstream_error` | Upstream 回傳非 2xx | ✅ |
| 499 | `client_disconnected` | Client 在 proxy 期間斷線；GPU lock 已釋放 | — |
| 504 | `request_timeout` | Proxy 超過 `max_proxy_seconds` 上限；GPU lock 已釋放 | ✅ |
| 400 | `missing_tenant` | 未提供 `X-Tenant-ID` header（佇列端點） | ❌ |
| 429 | `queue_full` | 每租戶佇列深度上限（10）已達 | ✅ |
| 409 | `task_not_cancellable` | 任務不是 `pending` 狀態，無法取消 | ❌ |

`insufficient_vram` 額外包含 `free_mb`（實際可用）與 `required_mb`（需求）。
`gpu_busy` 額外包含 `holder`（目前佔用 GPU 的模型 ID）。

---

## 設定格式

```yaml
gpu:
  index: 0               # GPU 裝置索引（預設：0）
  cooldown_seconds: 2    # 每次請求完成後，釋放 lock 前的等待秒數
  vram_headroom_mb: 1000 # VRAM preflight 額外保留的安全邊際（MB）

models:
  <模型ID>:
    route: /v1/images/generations   # 此模型處理的 URL 路徑
    upstream: http://image-api:8003 # 轉發目標
    uses_gpu: true                  # false = 跳過 lock/VRAM 檢查（預設：true）
    required_vram_mb: 12000         # 執行前需要的最低可用 VRAM（MB）
    max_proxy_seconds: 600          # 可選：proxy 最長時間（秒），超時回傳 504

    health:                         # 可選：等待 upstream 就緒
      type: http
      url: http://image-api:8003/health
      method: GET
      wait_timeout_seconds: 60

    unload:                         # 可選：在 GPU lock 取得後、proxy 前執行；釋放其他服務 VRAM
      - type: http
        url: http://other-api:8002/admin/unload
        timeout_seconds: 30
        headers:
          Authorization: Bearer ${OTHER_API_KEY}
      - type: http
        url: http://ollama:11434/api/generate
        body_json:
          model: llama3:8b
          keep_alive: 0

    cleanup:                        # 可選：在 GPU lock 釋放後執行；用來卸載此模型本身的 VRAM
      - type: http
        url: http://image-api:8003/admin/unload
        timeout_seconds: 30
        headers:
          Authorization: Bearer ${IMAGE_API_KEY}
```

多個模型可以共用同一個 `route` — request body 的 `model` 欄位決定套用哪個設定。
只有一個模型匹配路由時，`model` 欄位可省略。

頂層以 `x-` 開頭的 key 會被忽略（可用來定義 YAML anchors）。

所有字串值支援環境變數展開：`${VAR_NAME}`。

---

## 快速開始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test,nvml]"
pytest
gpu-arbiter --config examples/config.example.yaml --host 0.0.0.0 --port 8090 --db ./queue.db
```

## Docker Compose 部署

```yaml
services:
  gpu-arbiter:
    image: ghcr.io/datasys2026/gpu-arbiter:latest
    ports:
      - "8090:8090"
    volumes:
      - ./config/gpu-arbiter.yaml:/config/config.yaml:ro
    devices:
      - /dev/nvidia0
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

完整範例見 [`examples/docker-compose.yml`](./examples/docker-compose.yml) 與
[`examples/config.example.yaml`](./examples/config.example.yaml)。

## 文件

- [架構](./docs/architecture.zh-TW.md)
- [設定](./docs/configuration.zh-TW.md)
- [路由](./docs/routing.zh-TW.md)
- [錯誤碼](./docs/errors.zh-TW.md)
- [相容性](./docs/compatibility.zh-TW.md)
- [繁中導覽頁](./docs/index.zh-TW.md)
