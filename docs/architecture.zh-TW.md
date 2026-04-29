# 架構

GPU Arbiter 放在既有模型服務前面，作為一層窄而明確的控制平面。
它不直接擁有模型 runtime 本身。

## 元件

- 以 FastAPI 實作的 HTTP API
- 以記憶體內 GPU lock 做單程序序列化
- 以 NVML 實作的 VRAM probe 抽象
- 負責 unload / health 的 lifecycle hook runner
- 對指定 upstream 的 reverse proxy

## 設計目的

- 不改動既有模型後端
- 讓 GPU 競爭變成顯式且可重試
- 把 unload 與 health 視為一級生命週期步驟
- 保持程式碼小到可以直接跑在 Docker Compose stack

## 非目標

- 分散式排程
- 多節點叢集管理
- 模型註冊中心
- 多 GPU 自動放置與 bin-packing

