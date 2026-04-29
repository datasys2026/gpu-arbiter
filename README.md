# GPU Arbiter

GPU Arbiter 是一個給單機 Docker Compose AI 堆疊使用的輕量級 GPU runtime controller。
它負責序列化重型 GPU 任務、檢查可用 VRAM、執行生命週期 hooks、代理請求到既有模型服務，
並回傳清楚可重試的錯誤。

它不是 LiteLLM、Triton、KServe 或 GPUStack 的替代品。目標是單張 3090/4090 等級主機上，
同時跑圖像、語音、音樂等異質模型服務時，提供一層明確的控制平面。

## 語言

- 繁體中文：這份檔案
- English: [README.en.md](./README.en.md)

## 它解決什麼

- 在不同模型服務之間做全域 GPU 鎖
- 依模型或路由轉發到 upstream
- 在請求前做 NVML 相容的 VRAM 預檢
- 支援 `unload` 與 `health` 這類 HTTP lifecycle hooks
- 先卸載再載入的執行順序
- 清楚的可重試錯誤：`gpu_busy`、`insufficient_vram`、`upstream_error`
- 提供 health 與 model list 端點

## 請求流程

對每一條已設定的 GPU route，GPU Arbiter 會依序執行：

1. 從 request body 的 `model` 欄位或路由判定目標模型。
2. 取得全域 GPU lock。
3. 若有設定，先執行模型的 `unload` hook。
4. 若有設定，等待模型的 `health` hook 通過。
5. 檢查 `required_vram_mb` 是否小於目前可用 VRAM。
6. 將請求轉發到 upstream 服務。
7. 若有設定，完成後進入 cooldown 再釋放 lock。

這樣可以把模型生命週期管理從各自的服務中抽離出來，同時保留原本的 API 介面。

## 快速開始

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test,nvml]"
pytest
gpu-arbiter --config examples/config.example.yaml --host 127.0.0.1 --port 8090
```

## 範例設定

可先從 [examples/config.example.yaml](./examples/config.example.yaml) 開始。
如果要看內部 AIARK 的部署樣板，則看 [examples/config.aiark.yaml](./examples/config.aiark.yaml)。

## 文件

- [架構](./docs/architecture.zh-TW.md)
- [設定](./docs/configuration.zh-TW.md)
- [路由](./docs/routing.zh-TW.md)
- [錯誤碼](./docs/errors.zh-TW.md)
- [相容性](./docs/compatibility.zh-TW.md)
- [繁中導覽頁](./docs/index.zh-TW.md)

## 為什麼要做這個 repo

這個專案刻意維持小而明確。目標不是做成完整的模型託管平台，而是在既有模型服務前面加一層
控制平面，專門處理 GPU 鎖、生命週期 hooks 與可重試錯誤。
