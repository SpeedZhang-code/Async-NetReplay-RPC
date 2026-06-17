# `rpc_server.py` 文件說明

## 概述

`rpc_server.py` 是系統的 **JSON-RPC 2.0 服務層**，以 Werkzeug 作為 WSGI 伺服器載體，對外提供 RPC 方法供 Client 呼叫。收到開啟 Socket 服務的請求後，透過 Queue 通知 `main_server` 分配 Port，再由內部監聽執行緒接收分配結果並上報至 `web_server`。

---

## Import 技術詳解

### 標準函式庫

| 模組 | 用途 |
|------|------|
| `json` | 手動序列化錯誤回應（非 RPC 路由的 404 回應） |
| `threading` | 建立 `_notify_listener` 背景執行緒；保護 `_port_map` 的 Lock |
| `concurrent.futures.ThreadPoolExecutor` | 建立 RPC Worker Pool，限制最大並發 Socket 處理執行緒數 |

### 第三方套件

| 套件 | 引入內容 | 用途 |
|------|----------|------|
| `requests` | `requests` | 非同步 HTTP POST 上報事件至 `web_server`（fire-and-forget） |
| `werkzeug.wrappers` | `Request`, `Response` | 包裝 WSGI 請求/回應物件 |
| `werkzeug.serving` | `run_simple` | 啟動輕量 WSGI 開發伺服器（支援 `threaded=True`） |
| `jsonrpc` | `JSONRPCResponseManager`, `dispatcher` | 解析 JSON-RPC 2.0 請求、路由至對應 method、產生標準回應 |

---

## 全域變數說明

| 變數 | 型別 | 說明 |
|------|------|------|
| `worker_pool` | `ThreadPoolExecutor` | RPC Worker 執行緒池（由 `init_rpc_module` 初始化） |
| `rpc_queue_global` | `Queue` | 傳遞連線請求至 `main_server` |
| `rpc_notify_queue_global` | `Queue` | 接收 `main_server` 回傳的 Port 分配結果 |
| `max_socket_threads` | `int` | 全系統 Socket 執行緒上限（用於預檢） |
| `current_estimated_threads` | `int` | 本地估算的當前執行緒使用量（樂觀計數） |
| `_web_report_url` | `str` | 上報端點，預設 `http://127.0.0.1:8080/api/internal/socket-event` |
| `_port_map` | `dict[str, int]` | `client_id → port` 對照表，供 `get_assigned_port` 查詢 |
| `_port_map_lock` | `threading.Lock` | 保護 `_port_map` 的並發讀寫 |

---

## 功能模組說明

### 1. `_report()` — 非同步事件上報

```
事件觸發 → requests.post() → web_server /api/internal/socket-event
```

以 fire-and-forget 方式（`timeout=1`，捕捉所有例外）上報事件，不阻塞 RPC 主流程。

| 參數 | 說明 |
|------|------|
| `event` | 事件名稱（如 `rpc_discover`、`rpc_open_accepted`） |
| `client_id` | 觸發事件的 Client 識別碼 |
| `extra` | 附加資訊（如 `port`、`message`） |

---

### 2. `_notify_listener()` — Port 分配結果監聽執行緒

獨立 daemon 執行緒，持續阻塞等待 `rpc_notify_queue_global`，接收 `main_server` 的分配結果。

| 收到 `status` | 行為 |
|---------------|------|
| `ACCEPTED` | 寫入 `_port_map[client_id] = port`，上報 `rpc_open_accepted`（含 port）至 web_server |
| `REJECTED` | `current_estimated_threads` 減一（修正樂觀計數），上報 `rpc_open_rejected` |

> 此執行緒讓 `web_server` 的 `connected_clients` 能盡早寫入 `PENDING` 狀態，供 Client 輪詢。

---

### 3. JSON-RPC Methods

#### `discover()`

Client 發現服務時呼叫，回傳目前系統狀態。

```json
{
    "available_methods": ["discover", "open_socket_service", "get_assigned_port"],
    "socket_thread_limit": 5,
    "current_active_threads": 2,
    "status": "AVAILABLE"
}
```

---

#### `open_socket_service(**kwargs)`

Client 請求開啟 Socket 連線。Port 由 `main_server` PortPool 分配，**Client 不可自行指定**。

**流程：**

```
Client 呼叫
    │
    ├─ 預檢：current_estimated_threads >= max_socket_threads？
    │        └─ 是 → 回傳 REJECTED
    │
    ├─ 放入 rpc_queue → main_server 處理
    ├─ current_estimated_threads += 1（樂觀計數）
    │
    └─ 回傳 ACCEPTED（Port 尚未確定）
         └─ Client 應接著輪詢 get_assigned_port
```

| `kwargs` 參數 | 說明 |
|---------------|------|
| `client_id` | Client 自訂識別碼（預設 `"unknown_client"`） |
| `timestamp` | 選填，請求時間戳 |

---

#### `get_assigned_port(**kwargs)`

Client 收到 `ACCEPTED` 後輪詢，從 `_port_map` 直接取得分配到的 Port。

| 回傳 `status` | 說明 |
|---------------|------|
| `READY` | Port 已分配，回傳 `{ "status": "READY", "port": <int> }` |
| `PENDING` | `main_server` 尚未分配完成，請重試 |

---

### 4. `rpc_application` — Werkzeug WSGI 入口

使用 `@Request.application` 裝飾器，處理所有 HTTP 請求。

| 路由 | 方法 | 行為 |
|------|------|------|
| `/api/rpc` | `POST` | 交由 `JSONRPCResponseManager.handle()` 解析並路由至對應 dispatcher method |
| 其他 | 任意 | 回傳 JSON-RPC 標準錯誤 `{"code": -32601, "message": "Invalid Route"}`，HTTP 404 |

---

### 5. `init_rpc_module()` — 模組初始化入口

由 `main_server` 以子進程方式呼叫，完成以下初始化步驟：

```
1. 設定全域 Queue（rpc_queue、rpc_notify_queue）
2. 建構 web_server 上報 URL（依 args.host / args.web_port）
3. 初始化 ThreadPoolExecutor（worker_pool）
4. 啟動 _notify_listener daemon 執行緒
5. 呼叫 run_simple() 啟動 Werkzeug WSGI 服務
```

| 參數 | 說明 |
|------|------|
| `args` | 來自 `parse_arguments()` 的啟動參數 |
| `rpc_allocation` | 來自 `allocate_resources()` 的資源配額 |
| `rpc_queue` | Main → RPC 的連線請求 Queue |
| `rpc_notify_queue` | Main → RPC 的 Port 分配結果 Queue |

---

## Client 呼叫流程示意

```
Client
  │
  ├─ 1. discover()
  │       └─ 確認服務可用、取得 thread 限制資訊
  │
  ├─ 2. open_socket_service(client_id="abc")
  │       └─ 回傳 ACCEPTED（Port 尚未確定）
  │
  └─ 3. get_assigned_port(client_id="abc")  ← 輪詢直到 READY
          └─ 回傳 { "status": "READY", "port": 9091 }
```

---

## 內部事件流示意

```
open_socket_service()
      │
      ├─ rpc_queue.put(payload)
      │       └─► main_server 分配 Port
      │                   └─► rpc_notify_queue.put({"ACCEPTED", port})
      │
      └─ _notify_listener（背景執行緒）
              ├─ _port_map["abc"] = 9091
              └─ _report("rpc_open_accepted", port=9091) → web_server
```
