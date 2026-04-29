# 相容性

GPU Arbiter 是為單機 Docker Compose 部署而設計。

## 適合

- 本地 image、TTS、music 後端
- 已經有 HTTP API 的既有服務
- 單 GPU 機器，且同時間只應該跑一個重型工作

## 不適合

- 叢集排程器
- 多租戶模型託管平台
- 高併發推論叢集
- 需要跨多張 GPU 自動做 bin-packing 的系統

## 相關工具

- LiteLLM：OpenAI-compatible 的文字路由
- GPUStack：較完整的模型託管平台
- KServe：偏 Kubernetes-first 的推論平台
- Triton：高效能模型 serving

