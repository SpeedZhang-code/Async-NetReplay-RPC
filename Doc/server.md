# `main_server.py` 文件說明

## 概述

`main_server.py` 是整個系統的**主進程入口**，負責啟動並協調三個核心子進程（RPC Server、Flask Web Server、Socket Server），並透過多個 Queue 進行跨進程通訊，統一管理 Port 資源分配與 Socket 執行緒負載控制。

---

## Import 技術詳解

### 標準函式庫

| 模組 | 用途 |
|------|------|
| `multiprocessing` | 建立子進程（`Process`）、跨進程佇列（`Queue`） |
| `multiprocessing.connection.wait` | 零 CPU 消耗的多 Reader 事件監聽（類似 `select()`） |
| `os` | 取得當前 PID（`os.getpid()`） |
| `sys` | 程式安全退出（`sys.exit(0)`） |

### 自定義模組

| 模組 | 引入內容 | 說明 |
|------|----------|------|
| `rpc_server` | `init_rpc_module` | 初始化並啟動 RPC 服務進程 |
| `web_server` | `run_flask_server` | 啟動 Flask HTTP 網頁服務 |
| `socket_server` | `run_socket_server` | 啟動 Socket 長連線服務進程 |
| `config` | `parse_arguments` | 解析命令列引數 |
| `config` | `allocate_resources` | 根據引數計算各服務的 Process/Thread 分配 |
| `config` | `init_port_pool` | 初始化可用 Port 資源池 |

---

## 功能模組說明

### 1. 資源初始化

```
parse_arguments()     → 取得啟動參數
allocate_resources()  → 計算各服務資源配額
init_port_pool()      → 建立可用 Port 池
```

啟動時印出完整資源分配報告，包含 Flask Thread 數、RPC 控制執行緒數、Socket Pool 上限等。

---

### 2. 跨進程通訊佇列（Queue）

| Queue 名稱 | 方向 | 用途 |
|------------|------|------|
| `rpc_queue` | RPC → Main | RPC Server 傳入連線請求 |
| `web_queue` | Web → Main | Flask Server 傳入連線請求 |
| `release_queue` | Socket → Main | Socket 執行緒釋放通知（含 Port 回收） |
| `socket_cmd_queue` | Main → Socket | Main 指派 Socket 任務（`SPAWN_THREAD`） |
| `rpc_notify_queue` | Main → RPC | 通知 RPC 連線已被接受/拒絕及分配的 Port |

---

### 3. 核心子進程啟動

| 進程名稱 | 目標函式 | 說明 |
|----------|----------|------|
| `RPC_Server_Process` | `init_rpc_module` | 處理 RPC 連線請求，接收外部呼叫 |
| `Flask_Server_Process` | `run_flask_server` | 提供 HTTP Web 服務介面 |
| `Socket_Server_Process_1` | `run_socket_server` | 維持長連線 Socket 服務 |

---

### 4. 主事件迴圈（Main Event Loop）

使用 `multiprocessing.connection.wait(readers)` 監聽三條 Queue 的 Reader，**零 CPU 消耗**地等待事件觸發。

#### 事件處理邏輯

**① `release_queue` — Socket 執行緒釋放**
- 收到 `RELEASE_THREAD` 訊息
- `current_active_threads` 減一
- 將 `freed_port` 歸還給 `port_pool`

**② `rpc_queue` — RPC 連線請求**
- 檢查執行緒負載是否超過 `max_socket_threads`
- 從 `port_pool` 分配一個可用 Port
- 透過 `socket_cmd_queue` 發送 `SPAWN_THREAD` 指令給 Socket 進程
- 透過 `rpc_notify_queue` 回傳 `ACCEPTED`/`REJECTED` 及分配的 Port 給 RPC Server
- 負載計數 `current_active_threads` 加一

**③ `web_queue` — Web 連線請求**
- 流程與 RPC 相同，但不需要 `rpc_notify_queue` 回報
- 同樣檢查負載、分配 Port、派發 Socket 任務

---

### 5. 安全關閉機制

捕捉 `KeyboardInterrupt`（Ctrl+C），依序對所有子進程呼叫 `terminate()` + `join()`，確保資源乾淨釋放後退出。

---

## 架構示意圖

```
┌─────────────────────────────────────────────┐
│               main_server.py                │
│                                             │
│  ┌──────────┐   rpc_queue   ┌────────────┐  │
│  │ RPC Proc │ ────────────► │            │  │
│  └──────────┘               │            │  │
│                             │  Main      │  │  socket_cmd_queue
│  ┌──────────┐   web_queue   │  Event     │  ├──────────────────►  ┌─────────────┐
│  │Flask Proc│ ────────────► │  Loop      │  │                     │ Socket Proc │
│  └──────────┘               │            │  │◄─────────────────── └─────────────┘
│                             │            │  │  release_queue
│  rpc_notify_queue           │            │  │
│  ◄──────────────────────────│            │  │
│                             └────────────┘  │
└─────────────────────────────────────────────┘
```
