# 設定

設定檔使用 YAML。

## 頂層欄位

- `gpu.index`：傳給 NVML 的 GPU index
- `gpu.cooldown_seconds`：成功請求後的可選冷卻時間
- `models`：model id 到 model 設定的對應表

## 模型欄位

- `route`：arbiter 對外處理的路由
- `upstream`：upstream base URL
- `required_vram_mb`：請求開始前要求的最小 free VRAM
- `health`：可選的 HTTP hook，請求前先檢查
- `unload`：可選的 HTTP hook，請求前或轉發前先執行

## 環境變數

YAML 中的字串支援 `${NAME}` 形式的環境變數展開。

## 範例

- [examples/config.example.yaml](../examples/config.example.yaml)
- [examples/config.aiark.yaml](../examples/config.aiark.yaml)

