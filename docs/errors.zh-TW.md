# 錯誤碼

GPU Arbiter 會回傳結構化、可重試的錯誤。

## `gpu_busy`

GPU 已經被另一個工作佔用。

典型狀態碼：`409`

## `insufficient_vram`

目前可用 VRAM 不足以啟動設定的模型。

典型狀態碼：`503`

## `upstream_error`

arbiter 代理請求時，upstream 回傳非 2xx 狀態。

典型狀態碼：`502`

## `model_not_found`

沒有任何已設定模型能符合目前請求的路由或 `model` 欄位。

典型狀態碼：`404`

