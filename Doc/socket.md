# `socket_server.py` 文件說明

## 概述

`socket_server.py` 是系統的 **TCP Socket 長連線服務層**，以單一常駐進程搭配動態衍生執行緒的架構運作。進程本身只負責監聽 `main_server` 的指令 Queue；每當收到 `SPAWN_THREAD` 命令，便動態建立一條新執行緒，在指定 Port 上等待並服務單一 TCP Client，結束後將 Port 歸還給 `main_server` 的 PortPool。

---

## Import 技術詳解

### 標準函式庫

| 模組 | 用途 |
|------|------|
| `threading` | 動態衍生 Client 處理執行緒（`threading.Thread`）；取得執行緒 ID（`get_ident()`） |
| `socket` | 建立 TCP Server Socket（`AF_INET` + `SOCK_STREAM`）、`bind`、`listen`、`accept`、`recv`、`sendall` |
| `time` | 連線時間戳（`time.strftime`）；Socket 進程異常後的重試等待（`time.sleep(1)`） |

### 第三方套件

| 套件 | 用途 |
|------|------|
| `requests` | 非同步 HTTP POST 上報事件至 `web_server`（fire-and-forget） |

---

## 全域變數說明

| 變數 | 型別 | 說明 |
|------|------|------|
| `_web_report_url` | `str` | 上報端點，預設 `http://127.0.0.1:8080/api/internal/socket-event`，由 `run_socket_server` 依 args 覆寫 |

---

## 功能模組說明

### 1. `_report()` — 非同步事件上報

與 `rpc_server.py` 相同模式，fire-and-forget 上報至 `web_server`，`source` 固定為 `"SOCKET"`。

| `event` 值 | 觸發時機 |
|------------|----------|
| `connect` | Client TCP 連線建立成功 |
| `disconnect` | Client 主動斷線、逾時、或 `exit` 命令 |
| `message` | 收到訊息 / 回傳 Echo（各上報一次） |
| `error` | 執行緒內部例外 |

---

### 2. `handle_client_connection()` — Client 處理執行緒

每條 TCP 連線對應一條獨立執行緒，完整生命週期如下：

#### 啟動檢查
- 若 `client_data["port"]` 為 `None`（未正確注入），立即上報 `RELEASE_THREAD`（`port=None`）並退出。

#### Socket 建立階段

```python
socket.AF_INET      # IPv4
socket.SOCK_STREAM  # TCP
SO_REUSEADDR = 1    # 允許 Port 快速重用
bind("0.0.0.0", port)
listen(1)           # 單一 Client 佇列
settimeout(30.0)    # 等待連線限時 30 秒
```

#### 訊息迴圈（連線成功後）

```
client_conn.settimeout(None)   ← 取消超時，改為阻塞式等待

loop:
    recv(1024)
    ├─ 無資料     → disconnect（client_closed），break
    ├─ msg="exit" → disconnect（exit_command），break
    └─ 其他訊息   → 上報 message → Echo 回傳 → 上報 message（sent）
```

#### 結束與釋放（`finally` 區塊，一定執行）

```python
server_socket.close()
release_queue.put({"action": "RELEASE_THREAD", "port": port})
```

> `RELEASE_THREAD` 訊息是觸發 `main_server` 回收 Port 至 PortPool 的關鍵訊號。

#### 例外處理

| 例外 | 行為 |
|------|------|
| `socket.timeout` | 等待連線超時（30 秒），上報 `disconnect`（`reason: timeout`） |
| 其他 `Exception` | 上報 `error`，進入 `finally` 釋放資源 |

---

### 3. `run_socket_server()` — 常駐進程入口

單一 Socket 進程的主迴圈，阻塞等待 `socket_cmd_queue` 的指令。

#### 初始化
- 依 `args.host` 與 `args.web_port` 覆寫全域 `_web_report_url`

#### 指令處理

| `action` | 行為 |
|----------|------|
| `SPAWN_THREAD` | 取出 `cmd["data"]`，衍生 `handle_client_connection` daemon 執行緒 |

#### 例外處理

| 例外 | 行為 |
|------|------|
| `KeyboardInterrupt` | 跳出迴圈，進程安全退出 |
| 其他 `Exception` | 印出錯誤，`time.sleep(1)` 後繼續運作（避免進程崩潰） |

---

## 執行緒生命週期示意

```
run_socket_server（常駐進程）
      │
      │ socket_cmd_queue.get()
      │ cmd = { "action": "SPAWN_THREAD", "data": { client_id, port, ... } }
      │
      └─► threading.Thread(handle_client_connection)
                │
                ├─ bind(port) → listen → accept（等待 Client，最多 30 秒）
                │
                ├─ 連線成功
                │     ├─ _report("connect")
                │     └─ loop: recv → _report("message") → echo → _report("message")
                │
                ├─ 連線結束（斷線 / exit / 超時 / 異常）
                │     └─ _report("disconnect" / "error")
                │
                └─ finally
                      ├─ server_socket.close()
                      └─ release_queue.put({ "RELEASE_THREAD", port })
                                │
                                └─► main_server 回收 port → PortPool.release(port)
```

---

## 與其他模組的互動

| 方向 | 機制 | 內容 |
|------|------|------|
| `main_server` → `socket_server` | `socket_cmd_queue` | `SPAWN_THREAD` + `client_data`（含 port） |
| `socket_server` → `main_server` | `release_queue` | `RELEASE_THREAD` + `port`（歸還至 PortPool） |
| `socket_server` → `web_server` | HTTP POST | 連線狀態事件（connect / disconnect / message / error） |
