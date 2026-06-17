# `web_server.py` 文件說明

## 概述

`web_server.py` 是系統的 **監控中繼主控台層**，以 Flask 作為 HTTP 服務框架，提供三大功能：前端網頁入口、監控查詢 API、以及統一事件上報入口。`rpc_server` 與 `socket_server` 皆透過 HTTP POST 將事件推送至此，由內部 Worker Pool 非同步處理後寫入全域與個人事件日誌，並維護一份即時的 `connected_clients` 狀態機。

---

## Import 技術詳解

### 標準函式庫

| 模組 | 用途 |
|------|------|
| `os` | 取得當前 PID（`os.getpid()`）；組合 `index.html` 靜態檔案路徑 |
| `threading` | 建立 `monitor_lock`（`threading.Lock`），保護所有共享狀態的並發讀寫 |
| `time` | 產生事件時間戳（`time.strftime("%H:%M:%S")`） |
| `collections.deque` | 固定容量環形佇列，用於全域事件流（500 筆）與個人日誌（200 筆），自動淘汰舊資料 |
| `concurrent.futures.ThreadPoolExecutor` | 建立 Monitor Worker Pool，非同步處理事件寫入，避免阻塞 Flask 主執行緒 |

### 第三方套件

| 套件 | 引入內容 | 用途 |
|------|----------|------|
| `flask` | `Flask` | 建立 Web 應用實例，設定靜態資源目錄（`/web`） |
| `flask` | `jsonify` | 將 Python dict / list 序列化為 JSON HTTP 回應 |
| `flask` | `request` | 存取 HTTP 請求資料（Query String、JSON Body） |

---

## 全域變數說明

| 變數 | 型別 | 說明 |
|------|------|------|
| `_custom_thread_pool` | `ThreadPoolExecutor` | 事件處理 Worker Pool（由 `run_flask_server` 初始化） |
| `web_queue_global` | `Queue` | 接收 `main_server` 的 Web 連線請求（保留供擴充） |
| `monitor_lock` | `threading.Lock` | 保護所有共享狀態（`connected_clients`、`global_event_log`、`client_logs`） |
| `connected_clients` | `dict[str, dict]` | 目前在線 Client 狀態表，key 為 `client_id` |
| `global_event_log` | `deque(maxlen=500)` | 全域事件流，保留最近 500 筆 |
| `client_logs` | `dict[str, deque]` | 每個 Client 的專屬事件紀錄，各保留最近 200 筆 |
| `_event_seq` | `int` | 全域遞增事件序號，供前端去重與增量拉取 |

---

## 功能模組說明

### 1. Flask App 初始化

```python
app = Flask(__name__, static_folder='web', static_url_path='')
```

靜態資源（HTML / CSS / JS）放置於 `/web` 目錄，根路徑 `/` 直接映射。

---

### 2. 輔助函式

| 函式 | 說明 |
|------|------|
| `_next_seq()` | 全域事件序號遞增，每次呼叫 +1，前端以此做增量拉取（`?since=<seq>`） |
| `_ts()` | 回傳當前時間字串 `HH:MM:SS` |

---

### 3. 路由：前端入口

**`GET /`**

讀取 `/web/index.html`，以字串替換注入伺服器動態資訊後回傳。

| 模板變數 | 注入內容 |
|----------|----------|
| `{{ pid }}` | 當前進程 PID |
| `{{ total_threads }}` | 分配的總 Thread 數 |
| `{{ pool_threads }}` | Worker Pool Thread 數 |

---

### 4. 路由：監控 API

| 路由 | 方法 | 說明 |
|------|------|------|
| `/api/monitor/clients` | `GET` | 回傳 `connected_clients` 的所有 Client 清單 |
| `/api/monitor/log/global` | `GET` | 回傳全域事件流，支援 `?since=<seq>` 增量拉取 |
| `/api/monitor/log/client/<client_id>` | `GET` | 回傳指定 Client 的專屬事件紀錄，支援 `?since=<seq>` |

> `?since=<seq>` 參數讓前端只拉取新事件，減少傳輸量，預設值為 `0`（全量）。

---

### 5. 路由：統一事件上報入口

**`POST /api/internal/socket-event`**

`rpc_server` 與 `socket_server` 皆透過此端點推送事件。

#### 請求格式

```json
{
    "event":     "connect",
    "source":    "SOCKET",
    "client_id": "abc123",
    "message":   "...",
    ...事件專屬欄位
}
```

#### 支援事件類型

| 來源 | `event` 值 | 說明 |
|------|------------|------|
| RPC | `rpc_discover` | Client 發起服務發現 |
| RPC | `rpc_open_request` | Client 請求開啟 Socket 服務 |
| RPC | `rpc_open_accepted` | 主程式接受，回覆 ACCEPTED |
| RPC | `rpc_open_rejected` | 主程式拒絕，回覆 REJECTED |
| SOCKET | `connect` | Client TCP 連線建立 |
| SOCKET | `message` | 收到 / 回傳 Client 資料 |
| SOCKET | `disconnect` | Client 斷線（正常 / 超時 / 異常） |
| 通用 | `error` | 任何模組的錯誤訊息 |

#### 處理模式

| 狀態 | 行為 | HTTP 回應 |
|------|------|-----------|
| Worker Pool 已初始化 | `_custom_thread_pool.submit(_process_event)` 非同步處理 | `202 queued` |
| Worker Pool 未初始化 | 直接呼叫 `_process_event()` 同步處理 | `200 processed` |

---

### 6. `_process_event()` — 事件處理核心

在 Worker Thread 中執行，寫入日誌並更新狀態機。

```
接收 data
  │
  ├─ _make_label()       ← 產生人類可讀標籤
  ├─ _next_seq()         ← 取得唯一序號
  │
  └─ with monitor_lock:
       ├─ global_event_log.append(entry)        ← 寫入全域流
       ├─ client_logs[cid].append(entry)        ← 寫入個人流
       └─ _update_client_state()                ← 更新狀態機
```

#### `entry` 結構

| 欄位 | 說明 |
|------|------|
| `seq` | 全域遞增序號 |
| `ts` | 時間戳 `HH:MM:SS` |
| `event` | 事件名稱 |
| `source` | `RPC` 或 `SOCKET` |
| `client_id` | Client 識別碼 |
| `label` | 人類可讀標籤 |
| `message` | 附加訊息 |
| `raw` | 原始 payload |

---

### 7. `_update_client_state()` — Client 狀態機

根據事件更新 `connected_clients`，需在 `monitor_lock` 內呼叫。

| 事件 | 狀態機行為 |
|------|------------|
| `rpc_open_request` | 若 `cid` 不存在，建立 `PENDING` 暫時記錄 |
| `rpc_open_accepted` | 更新 port 欄位（由 `_process_event` 透過 `connect` 事件完成） |
| `connect` | 更新 `ip`、`port`，狀態改為 `CONNECTED` |
| `message` | 累加 `bytes_recv` / `bytes_sent` |
| `disconnect` | 從 `connected_clients` 移除該 Client |
| `rpc_open_rejected` | 若 Client 仍為 `PENDING` 狀態，直接移除 |

---

### 8. `run_flask_server()` — 模組初始化入口

由 `main_server` 以子進程方式呼叫。

```
1. 設定 web_queue_global
2. 計算 Worker Pool 大小 = max(1, web_threads - 1)
3. 初始化 ThreadPoolExecutor（MonitorWorkerPool）
4. 注入 app.config（TOTAL_THREADS、POOL_THREADS）
5. app.run（threaded=False，單執行緒模式，由 Worker Pool 承擔並發）
```

> Flask 以 `threaded=False` 啟動，避免與自定義 Worker Pool 衝突；事件處理並發由 `ThreadPoolExecutor` 負責。

---

## Client 狀態生命週期

```
rpc_open_request
      │
      ▼
  PENDING（connected_clients 建立）
      │
      ├─ rpc_open_rejected → 移除
      │
      ▼
  CONNECTED（connect 事件觸發）
      │
      ├─ message × N → bytes_recv / bytes_sent 累加
      │
      ▼
  disconnect → 移除
```

---

## 與其他模組的互動

| 來源 | 方向 | 機制 | 說明 |
|------|------|------|------|
| `rpc_server` | → `web_server` | HTTP POST `/api/internal/socket-event` | 上報 RPC 階段事件 |
| `socket_server` | → `web_server` | HTTP POST `/api/internal/socket-event` | 上報 Socket 連線事件 |
| 前端瀏覽器 | → `web_server` | HTTP GET 監控 API | 輪詢 Client 清單與事件日誌 |
