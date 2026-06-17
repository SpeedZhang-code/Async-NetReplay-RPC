import json
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
from werkzeug.wrappers import Request, Response
from werkzeug.serving import run_simple
from jsonrpc import JSONRPCResponseManager, dispatcher

# ==========================================
# 全域變數
# ==========================================
worker_pool               = None
rpc_queue_global          = None
rpc_notify_queue_global   = None   # 接收 main_server 回傳的 port 分配結果
max_socket_threads        = 0
current_estimated_threads = 0

_web_report_url = "http://127.0.0.1:8080/api/internal/socket-event"

# client_id → port 對照表（main_server 通知後寫入，供 web 上報使用）
_port_map: dict[str, int] = {}
_port_map_lock = threading.Lock()


def _report(event: str, client_id: str = None, extra: dict = None):
    """非同步上報事件至 web_server（fire-and-forget）"""
    payload = {"event": event, "source": "RPC", "client_id": client_id}
    if extra:
        payload.update(extra)
    try:
        requests.post(_web_report_url, json=payload, timeout=1)
    except Exception:
        pass


def _notify_listener():
    """
    獨立執行緒：持續監聽 rpc_notify_queue，
    接收 main_server 回傳的 port 分配結果，並上報 web_server。

    這樣 rpc_server 就能在 web_server 的 connected_clients 裡
    盡早寫入 port（status=PENDING），讓 client 輪詢時可以拿到。
    """
    global current_estimated_threads
    while True:
        try:
            msg = rpc_notify_queue_global.get()
            client_id = msg.get("client_id")
            status    = msg.get("status")
            port      = msg.get("port")

            if status == "ACCEPTED" and port:
                with _port_map_lock:
                    _port_map[client_id] = port

                # 上報 rpc_open_accepted（含 port）→ web_server 寫入 PENDING 記錄
                _report("rpc_open_accepted", client_id=client_id, extra={"port": port})
                print(f"[*] RPC_Notify: Client [{client_id}] 已分配 Port {port}，已上報 web_server。")

            elif status == "REJECTED":
                current_estimated_threads = max(0, current_estimated_threads - 1)
                _report("rpc_open_rejected", client_id=client_id, extra={
                    "message": "Rejected by main_server (limit or pool exhausted)."
                })
                print(f"[*] RPC_Notify: Client [{client_id}] 被 main_server 拒絕。")

        except Exception as e:
            print(f"[!] RPC_Notify 執行緒異常: {e}")


# ==========================================
# JSON-RPC Methods
# ==========================================

@dispatcher.add_method
def discover():
    global current_estimated_threads, max_socket_threads
    print("[*] RPC_Server: 收到 Client 服務發現 (discover) 請求")
    _report("rpc_discover")
    return {
        "available_methods":      ["discover", "open_socket_service", "get_assigned_port"],
        "socket_thread_limit":    max_socket_threads,
        "current_active_threads": current_estimated_threads,
        "status": "AVAILABLE" if current_estimated_threads < max_socket_threads else "FULL",
        "note":   "Port is assigned by server. Call get_assigned_port after ACCEPTED."
    }


@dispatcher.add_method
def open_socket_service(**kwargs):
    """
    Client 請求開啟 Socket 服務。
    Port 由 main_server PortPool 分配，不接受 client 傳入 port。
    """
    global rpc_queue_global, current_estimated_threads, max_socket_threads

    client_id = kwargs.get("client_id", "unknown_client")
    print(f"[*] RPC_Server: 收到 Client [{client_id}] 請求開啟 Socket 服務")

    _report("rpc_open_request", client_id=client_id)

    if current_estimated_threads >= max_socket_threads:
        _report("rpc_open_rejected", client_id=client_id, extra={
            "message": f"Server socket limits ({max_socket_threads}) reached."
        })
        return {
            "status":  "REJECTED",
            "message": f"Server socket limits ({max_socket_threads}) reached. Try again later."
        }

    try:
        rpc_payload = {
            "client_id": client_id,
            "timestamp": kwargs.get("timestamp", None),
        }
        rpc_queue_global.put(rpc_payload)
        current_estimated_threads += 1

        # 注意：此處先回 ACCEPTED，實際 port 由 _notify_listener 接收後上報
        # Client 應接著呼叫 get_assigned_port 輪詢
        return {
            "status":  "ACCEPTED",
            "message": "Request queued. Call get_assigned_port to retrieve your assigned port.",
        }

    except Exception as e:
        print(f"[!] RPC_Server 發送訊號至主行程失敗: {e}")
        _report("error", client_id=client_id, extra={"message": f"IPC error: {str(e)}"})
        return {"status": "ERROR", "message": f"IPC Pipeline error: {str(e)}"}


@dispatcher.add_method
def get_assigned_port(**kwargs):
    """
    Client 收到 ACCEPTED 後輪詢此 Method，直接從 rpc_server 的 _port_map 取得分配到的 port。
    比查詢 web_server API 更直接、更即時。

    回傳：
        READY:   { "status": "READY", "port": <int> }
        PENDING: { "status": "PENDING" }  ← main_server 尚未分配完成
    """
    client_id = kwargs.get("client_id", "unknown_client")
    with _port_map_lock:
        port = _port_map.get(client_id)

    if port:
        return {"status": "READY", "port": port}
    else:
        return {"status": "PENDING", "message": "Port not yet assigned, retry in a moment."}


# ==========================================
# Werkzeug WSGI 入口
# ==========================================
@Request.application
def rpc_application(request):
    if request.path == '/api/rpc' and request.method == 'POST':
        rpc_request_data = request.get_data(as_text=True)
        rpc_response = JSONRPCResponseManager.handle(rpc_request_data, dispatcher)
        return Response(rpc_response.json, mimetype='application/json')

    error_response = {
        "jsonrpc": "2.0",
        "error": {"code": -32601, "message": "Invalid Route"},
        "id": None
    }
    return Response(json.dumps(error_response), status=404, mimetype='application/json')


# ==========================================
# 模組初始化入口
# ==========================================
def init_rpc_module(args, rpc_allocation, rpc_queue, rpc_notify_queue):
    global worker_pool, rpc_queue_global, rpc_notify_queue_global
    global max_socket_threads, _web_report_url

    rpc_queue_global        = rpc_queue
    rpc_notify_queue_global = rpc_notify_queue

    web_host = args.host if args else "127.0.0.1"
    web_port = args.web_port if args else 8080
    _web_report_url = f"http://{web_host}:{web_port}/api/internal/socket-event"

    socket_worker_limit = rpc_allocation["worker_pool_threads"]
    max_socket_threads  = socket_worker_limit

    worker_pool = ThreadPoolExecutor(
        max_workers=socket_worker_limit,
        thread_name_prefix="RPC_Socket_Handler"
    )

    # 啟動 notify 監聽執行緒
    notify_thread = threading.Thread(target=_notify_listener, daemon=True, name="RPC_Notify_Listener")
    notify_thread.start()

    print(f"[✓] RPC Server 啟動於 {args.host if args else '0.0.0.0'}:{args.rpc_port if args else 5000}")
    print(f"    Web 上報端點: {_web_report_url}")
    
    run_simple(
        hostname=args.host if args else "0.0.0.0",
        port=args.rpc_port if args else 5000,
        application=rpc_application,
        threaded=True,
        use_reloader=False,
        use_debugger=False
    )