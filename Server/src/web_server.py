import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, request

# 初始化 Flask，並將網頁根目錄設定在 /web 下面
app = Flask(
    __name__,
    static_folder='web',
    static_url_path=''
)

# ==========================================
# 全域變數
# ==========================================
_custom_thread_pool = None
web_queue_global = None

# ==========================================
# 執行緒安全狀態儲存區
# ==========================================
monitor_lock = threading.Lock()

# 目前在線 clients：{ client_id: { ip, port, connected_at, source, bytes_sent, bytes_recv, status } }
connected_clients = {}

# 全域事件流：最多保留 500 筆，供「全部」頻道顯示
MAX_GLOBAL_LOG = 500
global_event_log = deque(maxlen=MAX_GLOBAL_LOG)

# 每個 client 的專屬訊息紀錄：{ client_id: deque(maxlen=200) }
MAX_CLIENT_LOG = 200
client_logs = {}

# 事件序號（前端用於去重）
_event_seq = 0

def _next_seq():
    global _event_seq
    _event_seq += 1
    return _event_seq

def _ts():
    return time.strftime("%H:%M:%S")

# ==========================================
# 前端入口
# ==========================================
@app.route('/')
def index():
    current_pid = os.getpid()
    total_threads = app.config.get('TOTAL_THREADS', 1)
    pool_threads  = app.config.get('POOL_THREADS', 0)
    try:
        base_path = os.path.abspath(os.path.dirname(__file__))
        static_html_path = os.path.join(base_path, app.static_folder, 'index.html')
        with open(static_html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        html_content = html_content.replace('{{ pid }}', str(current_pid))
        html_content = html_content.replace('{{ total_threads }}', str(total_threads))
        html_content = html_content.replace('{{ pool_threads }}', str(pool_threads))
        return html_content
    except Exception as e:
        return f"找不到前端 /web/index.html，錯誤: {str(e)}", 404

# ==========================================
# 監控 API
# ==========================================
@app.route('/api/monitor/clients', methods=['GET'])
def get_monitor_clients():
    """回傳目前在線 client 列表"""
    with monitor_lock:
        return jsonify(list(connected_clients.values()))


@app.route('/api/monitor/log/global', methods=['GET'])
def get_global_log():
    """
    回傳全域事件流。
    支援 ?since=<seq> 只拉取新事件，減少資料量。
    """
    since = int(request.args.get('since', 0))
    with monitor_lock:
        events = [e for e in global_event_log if e['seq'] > since]
    return jsonify(events)


@app.route('/api/monitor/log/client/<client_id>', methods=['GET'])
def get_client_log(client_id):
    """回傳指定 client 的專屬訊息紀錄"""
    since = int(request.args.get('since', 0))
    with monitor_lock:
        logs = client_logs.get(client_id, deque())
        events = [e for e in logs if e['seq'] > since]
    return jsonify(events)

# ==========================================
# 核心：統一事件上報入口
# ==========================================
@app.route('/api/internal/socket-event', methods=['POST'])
def handle_socket_event():
    """
    rpc_server 與 socket_server 皆透過此端點上報事件。

    共用事件格式：
    {
        "event":     <str>       # 見下方事件類型
        "source":    <str>       # "RPC" | "SOCKET"
        "client_id": <str>       # 唯一識別碼
        "message":   <str>       # 人類可讀訊息（可選）
        ...事件專屬欄位
    }

    事件類型 (event):
      RPC 來源：
        "rpc_discover"         - Client 發起服務發現
        "rpc_open_request"     - Client 請求開啟 Socket 服務
        "rpc_open_accepted"    - 主程式接受，回覆 ACCEPTED
        "rpc_open_rejected"    - 主程式拒絕，回覆 REJECTED

      SOCKET 來源：
        "connect"              - Client TCP 連線建立
        "message"              - 收到 Client 資料
        "disconnect"           - Client 斷線（正常/超時/異常）

      通用：
        "error"                - 任何模組的錯誤訊息
    """
    event_data = request.json or {}

    if _custom_thread_pool:
        _custom_thread_pool.submit(_process_event, event_data)
        return jsonify({"status": "queued"}), 202
    else:
        _process_event(event_data)
        return jsonify({"status": "processed"}), 200


def _process_event(data: dict):
    """在 Worker Thread 中處理事件，寫入全域與個人日誌"""
    event   = data.get('event', 'unknown')
    source  = data.get('source', 'UNKNOWN').upper()
    cid     = data.get('client_id')
    message = data.get('message', '')
    ts      = _ts()

    # 組合人類可讀標籤
    label = _make_label(event, source, data)

    entry = {
        "seq":       _next_seq(),
        "ts":        ts,
        "event":     event,
        "source":    source,
        "client_id": cid,
        "label":     label,
        "message":   message,
        "raw":       data,
    }

    with monitor_lock:
        # --- 寫入全域流 ---
        global_event_log.append(entry)

        # --- 寫入個人流 ---
        if cid:
            if cid not in client_logs:
                client_logs[cid] = deque(maxlen=MAX_CLIENT_LOG)
            client_logs[cid].append(entry)

        # --- 更新 connected_clients 狀態機 ---
        if cid:
            _update_client_state(event, source, data, cid, ts)


def _make_label(event: str, source: str, data: dict) -> str:
    """產生終端機顯示用的人類可讀標籤"""
    cid  = data.get('client_id', '?')
    port = data.get('port', '')
    ip   = data.get('ip', '')

    labels = {
        "rpc_discover":     f"[RPC] {cid} → discover()",
        "rpc_open_request": f"[RPC] {cid} → open_socket_service(port={port})",
        "rpc_open_accepted":f"[RPC] {cid} ← ACCEPTED (port={port})",
        "rpc_open_rejected":f"[RPC] {cid} ← REJECTED",
        "connect":          f"[SOCKET] {cid} 連線建立 {ip}:{port}",
        "message":          f"[SOCKET] {cid} → {data.get('message', '')}",
        "disconnect":       f"[SOCKET] {cid} 斷線 ({data.get('reason','normal')})",
        "error":            f"[{source}] ERROR: {data.get('message','')}",
    }
    return labels.get(event, f"[{source}] {event}")


def _update_client_state(event: str, source: str, data: dict, cid: str, ts: str):
    """根據事件更新 connected_clients 字典（已在 monitor_lock 內呼叫）"""
    if event == "rpc_open_request":
        # Client 第一次出現：先用 RPC 階段建立暫時記錄
        if cid not in connected_clients:
            connected_clients[cid] = {
                "client_id":    cid,
                "ip":           data.get('ip', '-'),
                "port":         data.get('port', '-'),
                "connected_at": ts,
                "source":       "RPC",
                "status":       "PENDING",   # 等待 Socket 建立
                "bytes_sent":   0,
                "bytes_recv":   0,
            }

    elif event == "connect":
        # Socket 連線真正建立，更新狀態
        if cid not in connected_clients:
            connected_clients[cid] = {
                "client_id":    cid,
                "ip":           data.get('ip', '-'),
                "port":         data.get('port', '-'),
                "connected_at": ts,
                "source":       "SOCKET",
                "status":       "CONNECTED",
                "bytes_sent":   0,
                "bytes_recv":   0,
            }
        else:
            connected_clients[cid].update({
                "ip":     data.get('ip', connected_clients[cid]['ip']),
                "port":   data.get('port', connected_clients[cid]['port']),
                "status": "CONNECTED",
                "source": "SOCKET",
            })

    elif event == "message":
        if cid in connected_clients:
            connected_clients[cid]['bytes_recv'] += data.get('recv', len(data.get('message', '')))
            connected_clients[cid]['bytes_sent'] += data.get('sent', 0)

    elif event == "disconnect":
        if cid in connected_clients:
            del connected_clients[cid]
        # 清理個人日誌保留但標記斷線（日誌本身已寫入，不需額外處理）

    elif event == "rpc_open_rejected":
        # 如果只在 PENDING 就被拒，直接移除
        if cid in connected_clients and connected_clients[cid].get('status') == 'PENDING':
            del connected_clients[cid]

# ==========================================
# 主程式啟動入口
# ==========================================
def run_flask_server(args, web_threads, web_queue):
    global _custom_thread_pool, web_queue_global
    web_queue_global = web_queue

    print(f"[✓] 中繼主控台伺服器進程已啟動 (PID: {os.getpid()})")

    allocated_pool_threads = max(1, web_threads - 1)
    _custom_thread_pool = ThreadPoolExecutor(
        max_workers=allocated_pool_threads,
        thread_name_prefix="MonitorWorkerPool"
    )

    app.config['TOTAL_THREADS'] = web_threads
    app.config['POOL_THREADS']  = allocated_pool_threads

    print(f"[*] 主控台啟動。主執行緒: 1，Worker Pool: {allocated_pool_threads} Threads。")

    app.run(
        host=args.host if args else "0.0.0.0",
        port=args.web_port if args else 5000,
        debug=False,
        threaded=False,
        use_reloader=False
    )