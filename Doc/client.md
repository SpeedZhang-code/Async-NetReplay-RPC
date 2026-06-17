# `client.py` 文件說明

## 概述

`client.py` 是整個系統的**外部測試客戶端**，以純標準函式庫實作，不依賴任何第三方套件。透過 JSON-RPC 2.0 與 `rpc_server` 協商，取得動態分配的 Port 後，再建立實體 TCP 長連線與 `socket_server` 進行訊息交換。

---

## Import 技術詳解

### 標準函式庫（全部）

| 模組 | 用途 |
|------|------|
| `json` | 手動序列化 JSON-RPC 2.0 請求 Payload；解析回應 |
| `socket` | 建立 TCP Client Socket（`AF_INET` + `SOCK_STREAM`）、`connect`、`sendall`、`recv` |
| `time` | 產生 RPC 請求 ID（毫秒時間戳）；步驟間等待（`time.sleep`）；Port 輪詢計時（`time.time()`） |
| `urllib.request` | 發送 HTTP POST 請求至 RPC 端點（無需安裝 `requests`） |

---

## 系統組態常數

| 常數 | 預設值 | 說明 |
|------|--------|------|
| `RPC_URL` | `http://127.0.0.1:5000/api/rpc` | JSON-RPC 端點 |
| `CLIENT_ID` | `"Client_Dynamic_9527"` | Client 唯一識別碼，貫穿整個流程 |
| `PORT_POLL_INTERVAL` | `0.3s` | 輪詢 `get_assigned_port` 的間隔 |
| `PORT_POLL_TIMEOUT` | `10.0s` | Port 分配等待上限，逾時中止 |

---

## 功能模組說明

### 1. `send_json_rpc(method, params)` — RPC 請求輔助

以 `urllib.request` 建構標準 JSON-RPC 2.0 請求，送至 `RPC_URL`。

```json
{
    "jsonrpc": "2.0",
    "method":  "discover",
    "params":  {},
    "id":      1700000000000
}
```

- `id` 以毫秒時間戳產生，避免碰撞
- `timeout=5`，失敗時回傳 `None`

---

### 2. `poll_for_assigned_port(client_id)` — Port 輪詢

反覆呼叫 `get_assigned_port` 直到取得 `status: READY`。

```
deadline = now + PORT_POLL_TIMEOUT

while now < deadline:
    resp = send_json_rpc("get_assigned_port", {client_id})
    if resp.result.status == "READY":
        return port
    sleep(PORT_POLL_INTERVAL)

return None  ← 超時
```

> 此設計對應 `rpc_server._notify_listener` 的非同步寫入時序，確保 Port 尚未就緒時不會錯誤地嘗試連線。

---

### 3. `main()` — Client 主邏輯（四步驟）

#### 步驟 1：服務發現 `discover()`

確認伺服器狀態與執行緒額度。若 `status == "FULL"` 則提早中止。

| 回應欄位 | 說明 |
|----------|------|
| `available_methods` | 可呼叫的 RPC Method 清單 |
| `socket_thread_limit` | 全系統 Socket 執行緒上限 |
| `current_active_threads` | 目前已使用額度 |
| `status` | `AVAILABLE` 或 `FULL` |

---

#### 步驟 2：請求開啟 Socket 服務 `open_socket_service()`

攜帶 `client_id` 與 `timestamp`，**不攜帶 port**（由伺服器 PortPool 統一分配）。

- 回應 `ACCEPTED` → 繼續下一步
- 回應非 `ACCEPTED` → 中止

---

#### 步驟 3：輪詢取得分配 Port

呼叫 `poll_for_assigned_port(CLIENT_ID)`，最多等待 10 秒。

成功取得 Port 後，額外等待 `0.3s` 緩衝，確保 `socket_server` 的執行緒已完成 `bind()` + `listen()`。

> **時序說明**：`rpc_notify_queue` 寫入 `_port_map` 與 `socket_server.bind()` 幾乎同時觸發，0.3s 緩衝足以覆蓋此極小時間差。

---

#### 步驟 4：建立 TCP 連線與訊息交換

```python
socket.connect(("127.0.0.1", assigned_port))
```

依序發送測試訊息，每則等待 1.5 秒：

| 訊息 | 說明 |
|------|------|
| `"Hello Server!"` | 一般訊息，Server 回傳 Echo |
| `"Heartbeat 01"` | 心跳測試 |
| `"Request Data Dump"` | 功能測試訊息 |
| `"exit"` | 觸發 Server 端斷線邏輯，結束迴圈 |

每則訊息以 `sendall()` 發送（加 `\n` 結尾），以 `recv(1024)` 接收 Echo 回應。

---

## 完整呼叫時序（對應 main_server.py 架構）

```
client.py          rpc_server       main_server       socket_server     web_server
    │                   │                 │                  │               │
    │─ discover() ─────►│                 │                  │               │
    │                   │────────────────────────────────────────────────────►│ rpc_discover
    │◄── AVAILABLE ─────│                 │                  │               │
    │                   │                 │                  │               │
    │─ open_socket() ──►│                 │                  │               │
    │                   │─ rpc_queue ────►│                  │               │
    │                   │────────────────────────────────────────────────────►│ rpc_open_request
    │◄── ACCEPTED ──────│                 │                  │               │
    │                   │                 │─ PortPool ──────►│               │
    │                   │                 │  .allocate()     │  bind(port)   │
    │                   │◄── rpc_notify_queue (port) ────────│               │
    │                   │  _port_map[cid]=port               │               │
    │                   │────────────────────────────────────────────────────►│ rpc_open_accepted
    │                   │                 │                  │               │
    │─ get_assigned_port() (輪詢) ────────│                  │               │
    │◄── { READY, port } ────────────────│                  │               │
    │                   │                 │                  │               │
    │─ socket.connect(port) ─────────────────────────────────►│              │
    │                   │                 │                  │  accept()     │
    │                   │                 │                  │───────────────►│ connect
    │                   │                 │                  │               │
    │─ sendall(msg) ─────────────────────────────────────────►│              │
    │◄── Echo ───────────────────────────────────────────────│               │
    │                   │                 │                  │───────────────►│ message
    │   ⋯ 重複 N 次 ⋯  │                 │                  │               │
    │                   │                 │                  │               │
    │─ sendall("exit") ──────────────────────────────────────►│              │
    │◄── Echo ───────────────────────────────────────────────│               │
    │                   │                 │  release_queue ◄─│               │
    │                   │                 │  PortPool        │               │
    │                   │                 │  .release(port)  │───────────────►│ disconnect
    │                   │                 │                  │               │
    │─ socket.close() ──────────────────────────────────────────────────────── 
```
