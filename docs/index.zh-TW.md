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

- [架構](./architecture.zh-TW.md)
- [設定](./configuration.zh-TW.md)
- [路由](./routing.zh-TW.md)
- [錯誤碼](./errors.zh-TW.md)
- [相容性](./compatibility.zh-TW.md)

## 建議閱讀順序

1. 先看 [README.md](../README.md) 了解整體目標。
2. 再看 [設定](./configuration.zh-TW.md) 了解 YAML 結構。
3. 接著看 [路由](./routing.zh-TW.md) 與 [錯誤碼](./errors.zh-TW.md)。
4. 最後看 [架構](./architecture.zh-TW.md) 與 [相容性](./compatibility.zh-TW.md)。
