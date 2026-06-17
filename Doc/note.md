# Python 程式碼筆記

> 用途：自我學習  
>
> 範圍：依據自定 dependencies 清單，逐套件拆解「baseline 寫法」和「進階 寫法」

---

## 目錄

1. [系統與執行環境（argparse / os / sys）](#1-系統與執行環境)
2. [多工與並行處理（multiprocessing / threading / concurrent.futures）](#2-多工與並行處理)
3. [網路與資料傳輸（json / socket / urllib.request）](#3-網路與資料傳輸)
4. [資料結構與時間（collections.deque / time）](#4-資料結構與時間)
5. [HTTP 客戶端（requests）](#5-http-客戶端requests)
6. [Web 框架與底層工具（Flask / Werkzeug）](#6-web-框架與底層工具flask--werkzeug)
7. [遠端程序呼叫（JSON-RPC）](#7-遠端程序呼叫json-rpc)
8. [總結：baseline vs 進階對照表](#8-總結baseline-vs-進階對照表)

---

## 1. 系統與執行環境

### 1.1 `argparse`

**Baseline 寫法**：只用 `add_argument` 接收幾個位置參數，不處理錯誤、不分組。

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("input")
parser.add_argument("--verbose", action="store_true")
args = parser.parse_args()
print(args.input, args.verbose)
```

**常用技巧**

- **子命令（subparsers）**：像 `git commit`、`git push` 那樣把工具拆成多個子指令，每個子指令有自己的參數集合。
  ```python
  parser = argparse.ArgumentParser(prog="mytool")
  sub = parser.add_subparsers(dest="command", required=True)

  p_train = sub.add_parser("train", help="訓練模型")
  p_train.add_argument("--epochs", type=int, default=10)

  p_infer = sub.add_parser("infer", help="推論")
  p_infer.add_argument("--checkpoint", required=True)

  args = parser.parse_args()
  if args.command == "train":
      ...
  ```
- **`type` 直接傳函式**做即時驗證/轉換，而不是先拿字串再手動轉型：
  ```python
  parser.add_argument("--rate", type=float)
  parser.add_argument("--config", type=argparse.FileType("r"))  # 直接拿到開好的檔案物件
  ```
- **`choices` + `nargs`** 限制輸入範圍，減少後續 if-else 驗證：
  ```python
  parser.add_argument("--mode", choices=["train", "eval", "predict"])
  parser.add_argument("--gpus", nargs="+", type=int)  # --gpus 0 1 2
  ```
- **`argparse.Namespace` 轉 dict**，方便丟給 `**kwargs` 或記錄成 log：
  ```python
  config = vars(args)
  ```
- **環境變數 fallback**：高手常讓 CLI 參數可被環境變數覆蓋預設值，方便 CI/CD 或容器化部署：
  ```python
  parser.add_argument("--api-key", default=os.environ.get("API_KEY"))
  ```

**常見陷阱**

- `action="store_true"` 的參數沒給 `default=False` 時其實沒問題（store_true 預設就是 False），但很多人誤以為要手動補上，徒增混亂。
- `nargs="*"` 與 `nargs="+"` 搞混：`*` 允許 0 個值，`+` 要求至少 1 個。
- 子命令忘記設 `dest`，導致無法用 `args.command` 判斷使用者選了哪個子命令。

**常問點**

- argparse vs click vs typer 的差異？（argparse 是標準庫零依賴；click/typer 語法更簡潔但需要額外安裝）
- 如何讓同一個程式同時支援 CLI 參數與設定檔，且 CLI 優先？（通常聽到「先讀設定檔成 dict，再用 argparse 結果 update 覆蓋」的分層覆蓋邏輯）

---

### 1.2 `os`

**Baseline 寫法**：用字串拼接路徑、用 `os.system` 執行指令。

```python
import os
path = os.getcwd() + "/" + "data" + "/" + "file.txt"  # 不推薦
os.system("ls -la")
```

**常用技巧**

- **一律用 `os.path.join` 或更進階的 `pathlib.Path`** 處理路徑，避免跨平台（Windows `\` vs Linux `/`）問題：
  ```python
  path = os.path.join(os.getcwd(), "data", "file.txt")
  ```
- **環境變數的安全讀取**：用 `os.environ.get(key, default)`，絕不直接 `os.environ[key]`（會在 key 不存在時整支程式崩潰），機敏資訊（API Key、密碼）一律走環境變數而非寫死在程式碼。
  ```python
  db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
  ```
- **`os.makedirs(path, exist_ok=True)`** 取代手動判斷資料夾是否存在再建立，避免 race condition：
  ```python
  os.makedirs("logs/2026", exist_ok=True)
  ```
- **用 `subprocess` 取代 `os.system`**：`os.system` 沒辦法拿到 stdout/stderr，也容易有 shell injection 風險；`subprocess.run` 才是現代正解：
  ```python
  import subprocess
  result = subprocess.run(["ls", "-la"], capture_output=True, text=True)
  print(result.stdout)
  ```
- **`os.cpu_count()`** 動態決定 worker 數量，而不是寫死 `processes=4`：
  ```python
  workers = os.cpu_count() or 1
  ```

**常見陷阱**

- 用 `os.system(f"rm {filename}")` 串接使用者輸入字串 → 典型 **shell injection** 漏洞，這也是常考的安全議題。
- 以為 `os.environ[key] = value` 設定的環境變數會傳給「父行程」或「其他已啟動的行程」——實際上只會影響當前行程及其之後產生的子行程。
- 混用相對路徑與絕對路徑，導致程式在不同工作目錄執行時行為不一致；高手會用 `os.path.abspath(__file__)` 取得腳本所在位置做基準。

**常問點**

- `os.system` 和 `subprocess.run` 差在哪？為什麼後者更安全？
- 如何讓程式不論在哪個目錄被呼叫都能正確找到自己同目錄下的資源檔？

---

### 1.3 `sys`

**Baseline 寫法**：只用 `sys.argv` 接收參數，或拿來 `print` 除錯。

```python
import sys
print(sys.argv[1])
```

**常用技巧**

- **`sys.exit(code)` 搭配明確的 exit code**：`0` 表示成功，非 0 表示各種失敗原因，方便 shell script 或 CI pipeline 用 `$?` 判斷：
  ```python
  if not validate(config):
      print("設定檔錯誤", file=sys.stderr)
      sys.exit(1)
  ```
- **錯誤訊息一律寫到 `sys.stderr`，正常輸出寫到 `sys.stdout`**，這樣使用者可以用 `program > out.log 2> err.log` 分開導向：
  ```python
  print("發生錯誤", file=sys.stderr)
  ```
- **`sys.path` 動態調整**：在套件結構複雜或需要臨時 import 某個路徑下模組時插入：
  ```python
  sys.path.insert(0, "/path/to/custom/modules")
  ```
- **`sys.modules` 檢查模組是否已載入**，常用於熱重載（hot reload）或避免重複初始化：
  ```python
  if "my_module" in sys.modules:
      del sys.modules["my_module"]
  ```
- **`sys.getsizeof()`** 用於記憶體分析，搭配 profiling 工具找出記憶體爆炸的根因。

**常見陷阱**

- 直接修改 `sys.path` 卻沒有用 `sys.path.insert(0, ...)`（用 `append` 的話如果有同名模組，可能載入到錯誤的版本）。
- 把 `sys.exit()` 當成一般的 `return` 在函式內到處呼叫，導致呼叫端無法用 `try/except SystemExit` 妥善處理，可測試性變差。

**常問點**

- `sys.exit()` 底層是怎麼運作的？（會丟出 `SystemExit` exception，可以被 `except` 攔截）
- `sys.argv` 與 `argparse` 的關係？（argparse 內部就是解析 `sys.argv`）

---

## 2. 多工與並行處理

> 最愛考的章節之一：核心是分清楚「CPU 密集型」用 multiprocessing，「I/O 密集型」用 threading 或 asyncio，選錯會直接被當場抓包。

### 2.1 `multiprocessing`

**Baseline 寫法**：建立幾個 `Process` 物件，手動 `start()` / `join()`。

```python
import multiprocessing

def worker(n):
    print(n * n)

processes = []
for i in range(4):
    p = multiprocessing.Process(target=worker, args=(i,))
    p.start()
    processes.append(p)
for p in processes:
    p.join()
```

**常用技巧**

- **改用 `multiprocessing.Pool`** 管理一批同質任務，避免手動管理 Process 列表：
  ```python
  with multiprocessing.Pool(processes=os.cpu_count()) as pool:
      results = pool.map(worker, range(100))
  ```
- **`Pool.imap` / `imap_unordered`** 取代 `map`：當任務數量很大且想邊算邊處理結果（而不是等全部算完才拿到 list）時，用 imap 可以邊跑邊消費，降低記憶體峰值：
  ```python
  for result in pool.imap_unordered(worker, big_iterable, chunksize=10):
      handle(result)
  ```
- **跨進程共享狀態**：`multiprocessing.Manager()` 提供可在不同進程間共享的 dict/list，或用 `Value` / `Array` 共享純量/陣列，但這些都有鎖開銷，高手通常盡量設計成「無共享狀態」的任務切分，而不是濫用共享記憶體：
  ```python
  with multiprocessing.Manager() as manager:
      shared_dict = manager.dict()
      shared_dict["count"] = 0
  ```
- **`multiprocessing.Queue`** 做進程間通訊（IPC），常用於 producer-consumer 模式：
  ```python
  q = multiprocessing.Queue()
  def producer(q):
      q.put("data")
  def consumer(q):
      print(q.get())
  ```
- **`if __name__ == "__main__":` 保護**：在 Windows（spawn 模式）下，子進程會重新 import 整個模組，沒有這個保護會無窮遞迴建立子進程，這是高手寫 multiprocessing 程式碼幾乎反射性會加上的防護。
- **明確指定 start method**：`multiprocessing.set_start_method("spawn")` 或 `"fork"`，因為 fork（Linux 預設）與 spawn（macOS/Windows 預設）的行為差異常常是跨平台 bug 的根源。

**常見陷阱**

- 在 Jupyter Notebook 或互動式環境直接用 multiprocessing，常因為 pickling 問題（lambda、closure 無法被序列化）而報錯。
- 任務本身很輕量（例如只是做一個加法）卻硬上多進程，導致「進程建立/通訊的開銷」遠大於「平行運算節省的時間」，效能反而變差——這是 GIL 迷思害人的典型案例，高手會先用 `timeit` 量過再決定要不要平行化。
- 共享資源（檔案、資料庫連線）沒有正確處理鎖定，導致多進程同時寫入造成資料損毀。

**常問點**

- 為什麼 CPU 密集型任務要用 multiprocessing 而不是 threading？（GIL 讓同一時間只有一個執行緒能執行 Python bytecode，multiprocessing 用獨立記憶體空間繞過 GIL）
- `fork` 與 `spawn` 的差異？
- 如何在多進程間安全地共享一個計數器？

---

### 2.2 `threading`

**Baseline 寫法**：建立 `Thread`，跑完用 `join()`。

```python
import threading

def fetch(url):
    print(f"fetching {url}")

threads = [threading.Thread(target=fetch, args=(url,)) for url in urls]
for t in threads:
    t.start()
for t in threads:
    t.join()
```

**常用技巧**

- **用 `Lock` / `RLock` 保護共享狀態**，避免 race condition：
  ```python
  lock = threading.Lock()
  counter = 0
  def increment():
      global counter
      with lock:
          counter += 1
  ```
- **`threading.Event`** 做執行緒間的信號通知（比起用一個被輪詢的 boolean 變數更省資源、更即時）：
  ```python
  stop_event = threading.Event()
  def worker():
      while not stop_event.is_set():
          do_work()
  stop_event.set()  # 通知所有 worker 停止
  ```
- **`threading.local()`** 建立執行緒專屬的資料容器，避免不同執行緒互相覆蓋對方的暫存資料（常見於資料庫連線、request context）：
  ```python
  local_data = threading.local()
  def handle():
      local_data.value = "thread-specific"
  ```
- **daemon thread**：設定 `t.daemon = True`，讓背景執行緒（如心跳檢測、log flush）在主程式結束時自動被終止，不會卡住整個程式退出。
- **理解何時 threading 真的有用**：I/O 等待（網路請求、檔案讀寫、資料庫查詢）時 GIL 會被釋放，所以 threading 對 I/O 密集型任務確實有平行效益；高手會明確區分「等待網路」這種情境適合 threading，「壓縮圖片」這種運算密集情境不適合。

**常見陷阱**

- 誤以為 threading 可以加速 CPU 密集型計算——GIL 讓多個 Python 執行緒無法同時跑 bytecode，CPU 密集任務用 threading 經常不會變快，甚至因 context switch 開銷變慢。
- 忘記加鎖就修改共享變數，產生難以重現的 race condition bug（這類 bug 常被問「如何 debug 一個間歇性出現的多執行緒錯誤」）。
- Deadlock：多個執行緒互相等待對方釋放鎖，常見於巢狀鎖定順序不一致時。

**常問點**

- 解釋 GIL（Global Interpreter Lock）是什麼，它如何影響 threading 的效能？
- Race condition 與 deadlock 的差異？如何避免？
- 什麼情境該用 threading，什麼情境該用 multiprocessing，什麼情境該用 asyncio？

---

### 2.3 `concurrent.futures.ThreadPoolExecutor`

**Baseline 寫法**：直接 submit 任務拿 future，逐一 `.result()`。

```python
from concurrent.futures import ThreadPoolExecutor

def fetch(url):
    return f"data from {url}"

with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(fetch, url) for url in urls]
    results = [f.result() for f in futures]
```

**常用技巧**

- **`executor.map()`** 在順序不重要時比手動 submit 更簡潔，且保留輸入順序對應輸出順序：
  ```python
  results = list(executor.map(fetch, urls))
  ```
- **`as_completed()`** 讓你「誰先做完就先處理誰」，而不是死等第一個任務完成（特別適合任務耗時差異很大的情境）：
  ```python
  from concurrent.futures import as_completed

  futures = {executor.submit(fetch, url): url for url in urls}
  for future in as_completed(futures):
      url = futures[future]
      try:
          data = future.result(timeout=10)
      except Exception as e:
          print(f"{url} failed: {e}")
  ```
- **設定 `timeout`**：`future.result(timeout=5)`，避免某個任務卡住拖垮整批工作，這是 production code 幾乎必備的防呆。
- **`ProcessPoolExecutor` 與 `ThreadPoolExecutor` 共用同一套 API**：會把任務函式寫成與 executor 無關，需要時只要換 import 就能從 thread 切換到 process 池，不用重寫邏輯。
- **動態調整 `max_workers`**：I/O 密集型任務可以開比 CPU 核心數多很多的 worker（例如 50~100），因為大部分時間都在等待網路，不消耗 CPU；但 CPU 密集型（配合 ProcessPoolExecutor）通常設成 `os.cpu_count()` 左右最划算。

**常見陷阱**

- 忘記用 `with` 語句管理 executor，導致程式結束時執行緒/進程沒有被正確清理。
- 在 `executor.map()` 中使用會丟例外的函式時，例外只會在你「迭代結果」時才被拋出，不是在 submit 當下，容易誤判任務已成功。
- 把 `ThreadPoolExecutor` 用在 CPU 密集任務上仍然受 GIL 限制，效能跟單執行緒差不多，這時應該換成 `ProcessPoolExecutor`。

**常問點**

- `ThreadPoolExecutor` 與手動管理 `threading.Thread` 相比，解決了什麼問題？（資源池化、避免無限建立執行緒、統一的 future 介面）
- `as_completed` 與 `map` 的使用情境差異？
- 如何在 80 個 API 請求中，限制最多 10 個併發，且任何一個超過 5 秒就跳過？（這題通常考 `ThreadPoolExecutor(max_workers=10)` + `future.result(timeout=5)` + `as_completed` 的組合）

---

## 3. 網路與資料傳輸

### 3.1 `json`

**Baseline 寫法**：直接 `json.dumps` / `json.loads`，不處理編碼、不處理特殊型別。

```python
import json

data = {"name": "Alice", "age": 30}
text = json.dumps(data)
parsed = json.loads(text)
```

**常用技巧**

- **自訂 `default` 處理無法序列化的型別**（如 `datetime`、自訂 class、`Decimal`）：
  ```python
  from datetime import datetime

  def default_serializer(obj):
      if isinstance(obj, datetime):
          return obj.isoformat()
      raise TypeError(f"無法序列化 {type(obj)}")

  json.dumps({"created_at": datetime.now()}, default=default_serializer)
  ```
- **`object_hook` 在反序列化階段就重建物件**，不用事後再轉換：
  ```python
  def as_datetime(d):
      if "created_at" in d:
          d["created_at"] = datetime.fromisoformat(d["created_at"])
      return d

  json.loads(text, object_hook=as_datetime)
  ```
- **大型 JSON 串流處理**：當 JSON 檔案大到無法整個讀進記憶體時，會用 `ijson` 之類的套件做串流解析，而不是硬上 `json.load`。
- **`ensure_ascii=False`** 輸出中文等非 ASCII 字元時保留原字元而非 `\uXXXX` 跳脫碼，對 debug 和檔案可讀性很重要：
  ```python
  json.dumps({"city": "新竹"}, ensure_ascii=False)
  ```
- **`indent` + `sort_keys`** 讓輸出穩定、易於 diff（尤其用在版本控制中的設定檔/快照測試）：
  ```python
  json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
  ```
- **效能敏感場景換成 `orjson` 或 `ujson`**：標準庫 `json` 在大量資料序列化時速度明顯慢於這些 C 實作的第三方套件，在高吞吐 API 服務中常會替換掉。

**常見陷阱**

- 用 `json.dumps` 序列化 `set`、`datetime`、自訂物件時直接報 `TypeError`，卻不知道要用 `default` 參數處理。
- 把 JSON 當成「型別安全」的格式來信任——JSON 沒有區分 int/float 在某些語言間的精度問題，也沒有原生支援日期，跨語言溝通時容易出現精度或格式不一致的 bug。
- 用字串相加手動拼 JSON 而不是用 `json.dumps`，極容易因為跳脫字元、引號問題產生格式錯誤的 JSON。

**常問點**

- JSON 與 Python dict 的型別對應關係（例如 JSON 沒有 tuple，會被轉成 array/list）。
- 如何序列化一個包含 `datetime` 欄位的物件？
- json 標準庫與 orjson 的效能差異與取捨？

---

### 3.2 `socket`

**Baseline 寫法**：建立一個陽春的 TCP echo server。

```python
import socket

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("0.0.0.0", 8080))
s.listen(1)
conn, addr = s.accept()
data = conn.recv(1024)
conn.send(data)
```

**常用技巧**

- **`setsockopt(SO_REUSEADDR)`** 避免重啟伺服器時遇到 "Address already in use" 錯誤：
  ```python
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  ```
- **用 `with socket.socket(...) as s:`** 確保 socket 一定會被正確關閉，即使中途發生例外。
- **處理 TCP 的「訊息邊界」問題**：TCP 是位元流（byte stream）協議，不保證一次 `recv()` 就能拿到完整訊息，會自己加上長度前綴（length-prefix framing）或用換行符號當分隔，並用迴圈持續 `recv` 直到收滿：
  ```python
  def recv_exact(conn, n):
      buf = b""
      while len(buf) < n:
          chunk = conn.recv(n - len(buf))
          if not chunk:
              raise ConnectionError("連線中斷")
          buf += chunk
      return buf
  ```
- **設定 timeout** 避免 `recv()` 永久阻塞：
  ```python
  s.settimeout(5.0)
  ```
- **搭配 `selectors` 模組**做單執行緒多連線的 I/O 多路復用（不想引入完整的 asyncio 框架時的折衷方案）。
- **理解什麼時候不該自己刻 socket**：知道大多數應用層需求（HTTP、WebSocket）應該用現成的協議庫（`requests`、`websockets`、`aiohttp`），自己刻 socket 通常只在寫底層協議、自訂二進位協議，或學習/考題情境才會出現。

**常見陷阱**

- 以為 `recv(1024)` 一定會收到完整的一筆訊息——這是最經典的新手誤解，TCP 沒有「訊息」概念，只有連續的位元流。
- 忘記處理 `recv()` 回傳空 bytes（`b""`）代表對方已關閉連線，導致無窮迴圈持續呼叫 `recv()`。
- Server 端沒有處理多連線（沒有用 thread/process/select/asyncio），導致只能服務一個 client，其他人連線會被卡住等待。

**常問點**

- 為什麼 TCP 的 `recv()` 不保證收到完整訊息？如何解決「訊息邊界」問題？
- TCP 與 UDP 的差異，各自適合什麼場景？
- 如何設計一個能同時處理多個連線的 socket server？（這題通常想聽到 thread-per-connection、process pool、或 select/epoll/asyncio 的取捨）

---

### 3.3 `urllib.request`

**Baseline 寫法**：用內建工具發一個 GET 請求。

```python
import urllib.request

response = urllib.request.urlopen("https://api.example.com/data")
data = response.read()
```

**常用技巧**

- **明確設定 timeout**，避免請求無限期卡住：
  ```python
  response = urllib.request.urlopen(url, timeout=5)
  ```
- **用 `Request` 物件自訂 headers 和 method**（PUT/DELETE/PATCH 等）：
  ```python
  req = urllib.request.Request(
      url,
      data=json.dumps({"key": "value"}).encode(),
      headers={"Content-Type": "application/json"},
      method="POST"
  )
  with urllib.request.urlopen(req) as resp:
      print(resp.status, resp.read())
  ```
- **用 `try/except urllib.error.HTTPError / URLError`** 分別處理「伺服器回了錯誤狀態碼」與「網路層面根本連不上」兩種不同情境：
  ```python
  from urllib.error import HTTPError, URLError
  try:
      urllib.request.urlopen(url, timeout=5)
  except HTTPError as e:
      print(f"HTTP 錯誤: {e.code}")
  except URLError as e:
      print(f"連線錯誤: {e.reason}")
  ```
- **理解何時該用 `urllib.request` vs `requests`**：知道 `urllib.request` 是零依賴的標準庫方案，適合不想引入第三方套件的輕量場景或受限環境（如某些嵌入式/沙盒環境）；一般應用開發絕大多數會選 `requests`（語法更簡潔、自動處理連線池、更好的例外體系）。

**常見陷阱**

- 沒設 timeout，請求對方伺服器掛掉或網路異常時整支程式卡死。
- 把 HTTPError 當成連不上網路的例外來處理——HTTPError 其實代表「成功連上了，但伺服器回了 4xx/5xx」，這跟 URLError（真正連不上）是不同的錯誤類型，混用會讓錯誤訊息誤導除錯方向。
- 用 `urllib.request` 處理需要 session/cookie 持久化的場景，寫起來比 `requests.Session()` 囉嗦很多，徒增維護成本。

**常問點**

- `urllib.request` 與 `requests` 的取捨？什麼情境下你會堅持只用標準庫？
- HTTPError 與 URLError 分別代表什麼？

---

## 4. 資料結構與時間

### 4.1 `collections.deque`

**Baseline 寫法**：把 deque 當成普通 list 使用，沒有用到它的優勢。

```python
from collections import deque

dq = deque()
dq.append(1)
dq.append(2)
print(dq.popleft())
```

**常用技巧**

- **理解效能特性再選用**：`list` 的 `pop(0)` 是 O(n)（需要搬移所有後續元素），而 `deque` 的 `popleft()` / `appendleft()` 都是 **O(1)**，這是高手選用 deque 而非 list 的核心理由——任何「兩端都要頻繁存取」的場景（佇列 Queue、滑動視窗、BFS）都該優先想到 deque。
- **`maxlen` 參數打造固定大小的滑動視窗 / 最近 N 筆記錄**，超出範圍會自動從另一端擠出舊資料，不用手動寫長度檢查：
  ```python
  recent_logs = deque(maxlen=100)  # 只保留最近 100 筆
  for log in incoming_logs:
      recent_logs.append(log)
  ```
- **BFS（廣度優先搜尋）的標準寫法**幾乎都用 deque 當佇列：
  ```python
  def bfs(graph, start):
      visited = {start}
      queue = deque([start])
      while queue:
          node = queue.popleft()
          for neighbor in graph[node]:
              if neighbor not in visited:
                  visited.add(neighbor)
                  queue.append(neighbor)
  ```
- **`rotate(n)`** 做環狀位移，常用在輪詢調度（round-robin）演算法：
  ```python
  dq.rotate(1)   # 往右轉一格：[1,2,3] -> [3,1,2]
  dq.rotate(-1)  # 往左轉一格
  ```
- **執行緒安全的單筆 append/pop**：deque 的單個 append/pop 操作本身是 thread-safe 的（GIL 保護），常被用作簡單的 producer-consumer 緩衝區，但要注意「檢查長度後再操作」這種多步驟邏輯仍需額外加鎖。

**常見陷阱**

- 對 deque 做隨機存取（`dq[100]`）時，效能是 O(n) 而不是 O(1)——deque 底層是雙向鏈結串列概念，不像 list 是連續記憶體陣列，不適合需要頻繁隨機索引的場景。
- 在 `maxlen` 已設定的 deque 上持續 append，沒意識到舊資料正在被悄悄丟棄，導致「資料消失」的詭異 bug。

**常問點**

- 為什麼 `list.pop(0)` 是 O(n) 而 `deque.popleft()` 是 O(1)？底層資料結構差異是什麼？
- 如何用 deque 實作一個「只保留最近 5 分鐘請求數」的 rate limiter？
- deque 適合用在 stack 嗎？（適合，append/pop 兩端都是 O(1)，但 Python 一般直接用 list 當 stack 也足夠，因為只用到尾端）

---

### 4.2 `time`

**Baseline 寫法**：用 `time.sleep` 延遲，`time.time()` 計算經過時間。

```python
import time

start = time.time()
time.sleep(1)
elapsed = time.time() - start
print(elapsed)
```

**常用技巧**

- **計算「經過時間」一律用 `time.perf_counter()` 而非 `time.time()`**：`time.time()` 回的是系統時鐘（wall clock），可能因系統時間同步（NTP）、夏令時間調整而跳動或倒退；`perf_counter()` 是單調遞增的高精度計時器，專門設計給效能測量用：
  ```python
  start = time.perf_counter()
  do_work()
  elapsed = time.perf_counter() - start
  ```
- **`time.monotonic()`** 用於需要「保證不會倒退」但不需要極高精度的場景（例如 timeout 判斷邏輯），語意上比 `perf_counter()` 更貼切表達「這是用來算經過時間，不是用來算絕對時刻」。
- **指數退避重試（exponential backoff）**：呼叫外部 API 失敗時，高手不會用固定間隔重試，而是讓等待時間隨重試次數指數增長，避免對方服務被打爆，也避免自己浪費資源：
  ```python
  def retry_with_backoff(fn, max_retries=5):
      for attempt in range(max_retries):
          try:
              return fn()
          except Exception:
              if attempt == max_retries - 1:
                  raise
              wait = (2 ** attempt) + random.uniform(0, 1)  # 加入 jitter 避免多個客戶端同時重試撞在一起
              time.sleep(wait)
  ```
- **絕對時刻 vs 經過時間分開處理**：要記錄「事件發生在幾點幾分」用 `datetime` 模組，要測量「花了多久」用 `perf_counter()`，高手不會混用這兩種語意。

**常見陷阱**

- 用 `time.time()` 來做效能 benchmark，在系統時間被 NTP 校正的瞬間可能得到負數或異常大的耗時數字。
- 在多執行緒/多進程程式中濫用 `time.sleep()` 做「輪詢等待」（polling）某個條件成立，而不是用 `threading.Event` 或 `Condition` 這種事件驅動機制，浪費 CPU 且反應延遲不穩定。
- 重試邏輯沒有設定「最大重試次數」或「最大總等待時間」，在持續失敗的情境下變成無窮迴圈。

**常問點**

- `time.time()`、`time.perf_counter()`、`time.monotonic()` 三者差異？
- 為什麼計算程式執行耗時不該用 `time.time()`？
- 解釋指數退避重試機制，為什麼要加 jitter（隨機抖動）？

---

## 5. HTTP 客戶端（requests）

**Baseline 寫法**：直接 `requests.get`/`post`，沒有處理 timeout、retry、connection reuse。

```python
import requests

response = requests.get("https://api.example.com/data")
print(response.json())
```

**常用技巧**

- **一律設定 `timeout`**：`requests` 預設「永不超時」，這是新手最容易踩的坑，高手寫的每一個 request 幾乎都會帶上 timeout：
  ```python
  response = requests.get(url, timeout=(3, 10))  # (連線 timeout, 讀取 timeout)
  ```
- **用 `requests.Session()` 重複使用連線**：當需要對同一個主機發多次請求時（例如分頁抓取、爬蟲），Session 會自動重用底層 TCP 連線（keep-alive）並保留 cookies，效能比每次都用 `requests.get` 好很多：
  ```python
  session = requests.Session()
  session.headers.update({"Authorization": f"Bearer {token}"})
  for page in range(1, 10):
      resp = session.get(f"{base_url}?page={page}", timeout=5)
  ```
- **搭配 `HTTPAdapter` + `Retry` 做自動重試**：對暫時性錯誤（如 503、連線重置）自動重試，且可設定指數退避：
  ```python
  from requests.adapters import HTTPAdapter
  from urllib3.util.retry import Retry

  retry_strategy = Retry(
      total=3,
      backoff_factor=1,
      status_forcelist=[429, 500, 502, 503, 504],
  )
  adapter = HTTPAdapter(max_retries=retry_strategy)
  session.mount("https://", adapter)
  session.mount("http://", adapter)
  ```
- **用 `response.raise_for_status()`** 讓 4xx/5xx 直接拋例外，而不是手動檢查 `if response.status_code != 200`，程式碼更簡潔且不會漏判：
  ```python
  try:
      response = session.get(url, timeout=5)
      response.raise_for_status()
  except requests.exceptions.HTTPError as e:
      print(f"HTTP 錯誤: {e}")
  except requests.exceptions.Timeout:
      print("請求超時")
  except requests.exceptions.ConnectionError:
      print("連線失敗")
  ```
- **串流下載大檔案**：用 `stream=True` + `iter_content`，避免把整個大檔案一次讀進記憶體：
  ```python
  with session.get(file_url, stream=True, timeout=30) as r:
      with open("large_file.zip", "wb") as f:
          for chunk in r.iter_content(chunk_size=8192):
              f.write(chunk)
  ```

**常見陷阱**

- 沒設 timeout，導致某次請求對方伺服器無回應時整支程式永久卡死。
- 在迴圈中每次都重新建立 `requests.get()` 而不是用 `Session`，喪失連線重用的效能優勢，且若對方需要登入態（cookies）還會直接出錯。
- 把 `response.json()` 直接呼叫卻沒先確認 `status_code` 或 `Content-Type`，遇到錯誤頁面（通常是 HTML）會直接拋 `JSONDecodeError`。

**常問點**

- `requests.get()` 沒設 timeout 會發生什麼事？為什麼這是個危險的預設值？
- 解釋 `Session` 的優勢，何時該用、何時不需要？
- 如何設計一個對暫時性錯誤自動重試、但對 4xx 客戶端錯誤不重試的 HTTP client？

---

## 6. Web 框架與底層工具（Flask / Werkzeug）

### 6.1 `Flask`

**Baseline 寫法**：寫幾個 route，直接回傳字典或字串。

```python
from flask import Flask, jsonify, request

app = Flask(__name__)

@app.route("/users/<int:user_id>")
def get_user(user_id):
    return jsonify({"id": user_id, "name": "Alice"})

if __name__ == "__main__":
    app.run(debug=True)
```

**常用技巧**

- **用 Blueprint 拆分大型應用**，避免所有 route 都塞在一個檔案：
  ```python
  from flask import Blueprint

  users_bp = Blueprint("users", __name__, url_prefix="/users")

  @users_bp.route("/<int:user_id>")
  def get_user(user_id):
      ...

  app.register_blueprint(users_bp)
  ```
- **用 `errorhandler` 統一錯誤格式**，而不是讓每個 route 各自處理錯誤：
  ```python
  @app.errorhandler(404)
  def not_found(e):
      return jsonify({"error": "Not Found"}), 404

  @app.errorhandler(Exception)
  def handle_exception(e):
      app.logger.exception(e)
      return jsonify({"error": "Internal Server Error"}), 500
  ```
- **用 `before_request` / `after_request` hook** 統一處理身份驗證、日誌記錄，而不是在每個 route function 內重複寫：
  ```python
  @app.before_request
  def authenticate():
      token = request.headers.get("Authorization")
      if not token:
          return jsonify({"error": "Unauthorized"}), 401
  ```
- **絕對不在生產環境用 `app.run(debug=True)`**：debug 模式會開啟互動式除錯器，攻擊者可能透過它在伺服器上執行任意程式碼；生產環境一律搭配 WSGI server（Gunicorn、uWSGI）部署：
  ```bash
  gunicorn -w 4 -b 0.0.0.0:8000 app:app
  ```
- **用 `request.get_json(silent=True)`** 安全地解析請求 body，避免格式錯誤時直接拋未處理的例外：
  ```python
  data = request.get_json(silent=True) or {}
  ```
- **應用工廠模式（Application Factory）**：把 Flask app 包成一個 `create_app()` 函式，方便測試時建立不同設定的 app 實例，也避免循環 import：
  ```python
  def create_app(config=None):
      app = Flask(__name__)
      app.config.from_object(config or "config.DevelopmentConfig")
      app.register_blueprint(users_bp)
      return app
  ```

**常見陷阱**

- 生產環境忘記關掉 `debug=True`，造成資安風險。
- 把資料庫連線、外部 API client 當成全域變數直接建立在模組層級，沒有考慮多執行緒/多進程下的連線管理，造成連線洩漏或競爭問題。
- route function 裡直接寫商業邏輯，沒有拆出 service layer，導致測試困難、程式碼難以重用。

**常問點**

- Flask 的 `debug=True` 有什麼資安風險？
- 如何設計一個可以同時支援測試環境與生產環境設定的 Flask 應用結構？
- Flask 的請求生命週期（request context、application context）是怎麼運作的？

---

### 6.2 `Werkzeug`（run_simple / Request / Response）

**Baseline 寫法**：很少有人直接碰 Werkzeug 底層 API，通常都是透過 Flask 間接使用。

```python
from werkzeug.wrappers import Request, Response

def application(environ, start_response):
    request = Request(environ)
    response = Response(f"Hello {request.args.get('name')}")
    return response(environ, start_response)
```

**常用技巧**

- **理解 Werkzeug 是 Flask 的地基**：Flask 的 Request/Response 物件其實就是繼承自 Werkzeug，高手知道遇到 Flask 文件沒寫清楚的細節（如 headers 處理、cookie 簽章），常常要去翻 Werkzeug 原始碼或文件才找得到答案。
- **直接用 `run_simple` 寫一個極簡 WSGI app 做本地測試或教學示範**，不依賴完整 Flask 框架的開銷：
  ```python
  from werkzeug.serving import run_simple

  run_simple("localhost", 8080, application, use_reloader=True, use_debugger=True)
  ```
- **善用 Werkzeug 的 routing 系統（`Map`/`Rule`）**：當需要寫一個輕量級、不想引入完整框架的 WSGI 應用時，Werkzeug 本身就提供完整的路由比對功能。
- **理解 WSGI 協議本質**：`application(environ, start_response)` 這個簽名就是 WSGI 規範定義的標準介面，所有 Python web 框架（Flask、Django）底層都遵循這個協議，這也是常考的基礎概念，能解釋這點代表對 Python web 生態系有紮實理解。

**常見陷阱**

- 把 `run_simple` 開的開發伺服器直接部署上生產環境——它跟 Flask 的 `app.run()` 一樣只適合開發測試，不具備生產環境需要的併發處理能力與穩定性。
- 不理解 WSGI 的同步阻塞本質，誤以為它能像 ASGI（如 FastAPI 用的）一樣原生支援 async/await。

**常問點**

- 解釋 WSGI 協議的基本介面（`environ`, `start_response`）。
- Flask 與 Werkzeug 的關係是什麼？
- WSGI 與 ASGI 的差異？為什麼非同步框架需要 ASGI？

---

## 7. 遠端程序呼叫（JSON-RPC）

**Baseline 寫法**：用 `dispatcher` 註冊函式，直接回應請求。

```python
from jsonrpc import JSONRPCResponseManager, dispatcher

@dispatcher.add_method
def add(a, b):
    return a + b

response = JSONRPCResponseManager.handle(request_json, dispatcher)
```

**常用技巧**

- **結合 Werkzeug 把 JSON-RPC 包成一個完整的 WSGI 服務**，這正是這份 dependencies 清單裡 `jsonrpc` + `werkzeug` 經常搭配出現的原因：
  ```python
  from werkzeug.wrappers import Request, Response
  from werkzeug.serving import run_simple
  from jsonrpc import JSONRPCResponseManager, dispatcher

  @dispatcher.add_method
  def echo(message):
      return message

  @Request.application
  def application(request):
      response = JSONRPCResponseManager.handle(request.data, dispatcher)
      return Response(response.json, mimetype="application/json")

  run_simple("0.0.0.0", 4000, application)
  ```
- **明確處理錯誤回應**：JSON-RPC 規範定義了標準錯誤碼（如 `-32601` Method not found），高手會讓自訂例外正確映射到這些標準錯誤碼，而不是讓所有錯誤都變成籠統的 500：
  ```python
  from jsonrpc.exceptions import JSONRPCDispatchException

  @dispatcher.add_method
  def divide(a, b):
      if b == 0:
          raise JSONRPCDispatchException(code=400, message="除數不能為零")
      return a / b
  ```
- **為什麼選 JSON-RPC 而不是 REST**：高手能清楚說明取捨——JSON-RPC 適合「以動作/方法為中心」的呼叫模式（像呼叫遠端函式一樣自然），且天生支援 **batch request**（一次送多個呼叫，減少來回延遲），這在 REST 設計裡通常要額外設計才能做到；REST 則更適合「以資源為中心」、需要善用 HTTP 語意（GET/POST/PUT/DELETE 對應 CRUD）與 HTTP 快取機制的場景。
- **批次請求（batch）**：JSON-RPC 2.0 規範允許把多個請求包成一個 JSON array 一次送出，伺服器端 `JSONRPCResponseManager.handle` 通常會自動處理 batch 格式並回傳對應的 array 結果，高手會在「需要一次呼叫多個方法、減少 round-trip」的場景善用這個特性。

**常見陷阱**

- 把所有例外都包成籠統的錯誤訊息，前端/呼叫端無法依據錯誤碼做不同處理。
- 沒有對輸入參數做驗證，直接信任 RPC 呼叫傳入的參數型別與內容，造成執行階段錯誤或安全隱患。
- 忽略 JSON-RPC 的版本欄位（`"jsonrpc": "2.0"`）與請求 id 對應機制，導致非同步情境下無法正確比對請求與回應。

**常問點**

- JSON-RPC 與 REST API 的設計哲學差異？什麼情境你會選 JSON-RPC？
- 如何在 JSON-RPC 服務中設計妥善的錯誤處理機制？
- 解釋 JSON-RPC 的 batch request 機制，它解決了什麼問題？

---

## 8. 總結：baseline vs 進階對照表

| 主題 | Baseline 心態 | 高手進階技巧 |
|------|---------------|----------|
| 並行處理選型 | 看到「平行」就反射性開多進程或多執行緒 | 先判斷任務是 CPU 密集還是 I/O 密集，再決定 multiprocessing / threading / asyncio |
| 網路請求 | 直接呼叫，不設 timeout | 永遠設 timeout，並規劃 retry 與錯誤分類處理 |
| 路徑/環境變數 | 字串拼接、直接索引 `os.environ[key]` | `os.path.join`/`pathlib`，`os.environ.get(key, default)` |
| 資料結構選用 | 不管場景一律用 list | 依存取模式選擇（兩端存取頻繁 → deque；需要去重 → set；需要排序鍵值 → dict + 排序） |
| 計時 | 用 `time.time()` 量效能 | 用 `time.perf_counter()`，理解 wall clock 與 monotonic clock 的差異 |
| Web 服務部署 | `debug=True` 跑 `app.run()` 上生產 | WSGI server（Gunicorn）+ 關閉 debug + 統一錯誤處理 |
| 例外處理 | 一個籠統的 `except Exception` | 依錯誤類型分層處理（連線錯誤 / 業務錯誤 / 驗證錯誤），並映射到對應的錯誤碼 |
| 程式碼結構 | 所有邏輯塞在一個檔案/函式 | 依職責拆分（Blueprint、service layer、application factory） |
| 重試邏輯 | 失敗就無限迴圈重試 | 指數退避 + jitter + 最大重試次數上限 |
| 安全意識 | 信任所有輸入、字串拼接組指令 | 永遠驗證輸入、用參數化/陣列形式呼叫子行程，避免 injection |

> **準備建議**：被問到某個套件時，先講 baseline 用法展示你「會用」，再主動補一句「但在 production / 高併發場景，我會考慮 ___」展示你「懂取捨」，這通常是區分 junior 和 senior 回答的關鍵分水嶺。
