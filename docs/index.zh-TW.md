# GPU Arbiter 文件導覽

這份導覽頁給繁體中文讀者快速對齊這個專案的定位與文件入口。

## 這是什麼

GPU Arbiter 是一個放在既有模型服務前面的控制平面，主要處理：

- 單機 GPU 鎖
- VRAM 預檢
- unload / health lifecycle hooks
- 將請求轉發到 upstream
- 回傳可重試的結構化錯誤

## 文件入口

- [架構](./architecture.md)
- [設定](./configuration.md)
- [路由](./routing.md)
- [錯誤碼](./errors.md)
- [相容性](./compatibility.md)

## 建議閱讀順序

1. 先看 [README.zh-TW.md](../README.zh-TW.md) 了解整體目標。
2. 再看 [設定](./configuration.md) 了解 YAML 結構。
3. 接著看 [路由](./routing.md) 與 [錯誤碼](./errors.md)。
4. 最後看 [架構](./architecture.md) 與 [相容性](./compatibility.md)。

