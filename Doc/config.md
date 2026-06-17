# `config.py` 文件說明

## 概述

`config.py` 是整個系統的**配置與資源管理核心**，提供三大功能：命令列參數解析、執行緒/進程資源分配演算法、以及執行緒安全的 Port 資源池（`PortPool`）。由 `main_server.py` 在啟動時初始化，其他模組可透過 `get_port_pool()` 取得全域單例。

---

## Import 技術詳解

### 標準函式庫

| 模組 | 用途 |
|------|------|
| `sys` | 偵測是否在 Jupyter 環境（`ipykernel_launcher`）以決定是否忽略命令列引數 |
| `argparse` | 建立命令列參數解析器，定義各服務的預設 IP、Port、Thread/Process 上限 |
| `threading` | 提供 `threading.Lock`，保護 `PortPool` 的 `allocate()` / `release()` 在多執行緒環境下的資料一致性 |

---

## 功能模組說明

### 1. `parse_arguments()` — 命令列參數解析

解析啟動參數，回傳 `args` 物件供後續模組使用。

| 參數 | 型別 | 預設值 | 說明 |
|------|------|--------|------|
| `--host` | `str` | `127.0.0.1` | 伺服器監聽 IP |
| `--rpc-port` | `int` | `5000` | RPC 服務埠號 |
| `--web-port` | `int` | `8080` | Flask 網頁埠號 |
| `--socket-port` | `int` | `9090` | Socket 服務起始埠號（往上遞增） |
| `--thread-num` | `int` | `12` | 系統 Thread 總上限 |
| `--process-num` | `int` | `4` | Worker Process 總上限 |

> **Jupyter 相容性**：若在 `ipykernel_launcher` 環境中執行，自動傳入空引數避免解析錯誤。

---

### 2. `allocate_resources(args)` — 資源分配演算法（第六版）

根據 `args.thread_num` 計算各服務的執行緒/進程配額，採**固定進程 + 平均分配剩餘執行緒**策略。

#### 進程分配（固定）

| 服務 | 進程數 |
|------|--------|
| Flask Web | 1 |
| RPC Server | 1 |
| Socket Server | 1 |

#### 執行緒分配邏輯（依序扣除）

```
total_threads
  └─ Flask Web Threads     = max(4, total // 4)          ← 優先分配 25%，最少 4 個
  └─ RPC Fixed Overhead    = 2                            ← werkzeug(1) + 核心控制(1)
  └─ remaining             = total - web_threads - 2
       ├─ RPC Worker Pool  = remaining // 2
       └─ Socket Pool      = remaining // 2 + (餘數)     ← 除不盡時餘數補給 Socket
```

#### 回傳結構

```python
{
    "rpc": {
        "procs": 1,
        "werkzeug_threads": 1,
        "control_threads": 1,
        "worker_pool_threads": N
    },
    "web": {
        "procs": 1,
        "threads": N
    },
    "socket_pool": {
        "procs": 1,
        "threads": N          # 同時也是 PortPool 的 capacity
    }
}
```

---

### 3. `PortPool` — 執行緒安全 Port 資源池

管理 Socket 服務使用的動態 Port 池，確保多執行緒環境下 Port 不重複分配。

#### 初始化

```python
PortPool(base_port=9090, capacity=5)
# → 產生可用 Port: [9090, 9091, 9092, 9093, 9094]
```

#### 方法

| 方法 | 回傳 | 說明 |
|------|------|------|
| `allocate()` | `int \| None` | 取出一個可用 Port；池空時回傳 `None` |
| `release(port)` | 無 | 歸還 Port 至可用清單尾端 |
| `status()` | `dict` | 回傳 `available`、`in_use`、`capacity`（供 debug 用） |
| `__repr__()` | `str` | 顯示池的基本狀態摘要 |

#### 執行緒安全機制

所有讀寫操作皆透過 `threading.Lock` 保護，`allocate()` 和 `release()` 使用 `with self._lock` 確保原子性。

#### 內部資料結構

| 屬性 | 型別 | 說明 |
|------|------|------|
| `_available` | `list[int]` | 目前可分配的 Port 清單（FIFO） |
| `_in_use` | `set[int]` | 目前已被佔用的 Port 集合 |
| `_lock` | `threading.Lock` | 保護並發存取 |

---

### 4. 全域管理

| 函式 | 說明 |
|------|------|
| `init_port_pool(args, allocation)` | 由 `main_server` 啟動時呼叫一次，建立全域 `PortPool`；`capacity` 取自 `socket_pool.threads` |
| `get_port_pool()` | 取得全域單例；若未初始化則拋出 `RuntimeError` |

> 模組層級變數 `_port_pool` 預設為 `None`，強制要求先呼叫 `init_port_pool()` 才能使用。

---

## Port 生命週期

```
init_port_pool()
      │
      ▼
  PortPool 建立
  _available = [9090, 9091, 9092, ...]
  _in_use    = {}

      │  allocate()
      ▼
  port = 9090 → 移入 _in_use

      │  (Client 連線中...)

      │  release(9090)
      ▼
  9090 → 移回 _available 尾端
```
