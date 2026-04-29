# 路由

arbiter 會用兩種方式解析請求：

1. 將 request body 的 `model` 欄位對到已設定的 model id。
2. 將 request path 對到已設定的 `route`。

如果兩者同時存在，明確指定的 `model` id 會優先。

## 請求順序

對於已命中的 route，arbiter 會依序：

1. 取得全域 GPU lock。
2. 執行設定好的 unload hook。
3. 等待設定好的 health hook。
4. 檢查可用 VRAM。
5. 把請求轉發到 upstream 服務。

## 路徑處理

arbiter 會把原始 method、body、headers 與 query string 轉發給 upstream，
但會移除像 `Host` 與 `Content-Length` 這類 hop-by-hop headers。

