import json
import socket
import time
import urllib.request

# ==========================================
# 系統組態設定
# ==========================================
RPC_URL   = "http://127.0.0.1:5000/api/rpc"
CLIENT_ID = "Client_Dynamic_9527"

PORT_POLL_INTERVAL = 0.3   # 每次輪詢間隔（秒）
PORT_POLL_TIMEOUT  = 10.0  # 最長等待 port 分配的時間（秒）


# ==========================================
# 通訊輔助函式
# ==========================================
def send_json_rpc(method, params=None):
    """發送標準 JSON-RPC 2.0 請求給 RPC_server。"""
    payload = {
        "jsonrpc": "2.0",
        "method":  method,
        "params":  params or {},
        "id":      int(time.time() * 1000)
    }
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(
        RPC_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"[!] 無法與 RPC_Server 進行通訊: {e}")
        return None


def poll_for_assigned_port(client_id, timeout=PORT_POLL_TIMEOUT, interval=PORT_POLL_INTERVAL):
    """
    輪詢 RPC get_assigned_port，直到取得 READY + port。

    流程：
      open_socket_service → main_server 分配 port → rpc_notify_queue
      → rpc_server._notify_listener 寫入 _port_map
      → get_assigned_port 回傳 READY
    
    回傳 port (int) 或 None（超時）。
    """
    deadline = time.time() + timeout
    attempt  = 0

    while time.time() < deadline:
        attempt += 1
        resp = send_json_rpc("get_assigned_port", {"client_id": client_id})

        if resp and "result" in resp:
            result = resp["result"]
            if result.get("status") == "READY":
                port = result.get("port")
                if isinstance(port, int):
                    return port

        print(f"    [等待] 第 {attempt} 次輪詢，port 尚未就緒，{interval}s 後重試...")
        time.sleep(interval)

    return None


# ==========================================
# Client 主邏輯流程
# ==========================================
def main():
    print(f"[*] {CLIENT_ID} 啟動，準備與後端系統進行握手...")

    # --------------------------------------------------------
    # 步驟 1：服務發現 (discover)
    # --------------------------------------------------------
    print("\n[步驟 1] 發送 discover 請求，查詢伺服器可用方法與執行緒額度...")
    rpc_response = send_json_rpc("discover")

    if not rpc_response or "result" not in rpc_response:
        print("[!] 服務發現失敗，中止程序。")
        return

    result = rpc_response["result"]
    print(f"    ├── 可用方法群 : {result.get('available_methods')}")
    print(f"    ├── 總執行緒上限: {result.get('socket_thread_limit')} Threads")
    print(f"    ├── 目前已用額度: {result.get('current_active_threads')} Threads")
    print(f"    ├── 伺服器狀態 : {result.get('status')}")
    print(f"    └── 備註       : {result.get('note', '-')}")

    if result.get("status") == "FULL":
        print("[!] 伺服器 Socket 額度已滿，無法請求開啟服務，通訊終止。")
        return

    time.sleep(1)

    # --------------------------------------------------------
    # 步驟 2：請求開啟 Socket 服務 (open_socket_service)
    #         不帶 port，由伺服器 PortPool 統一分配
    # --------------------------------------------------------
    print(f"\n[步驟 2] 呼叫 open_socket_service，請求伺服器動態分配 Socket 通道...")
    rpc_response = send_json_rpc("open_socket_service", {
        "client_id": CLIENT_ID,
        "timestamp": time.time()
    })

    if not rpc_response or "result" not in rpc_response:
        print("[!] 請求開啟 Socket 服務失敗，中止程序。")
        return

    conn_result = rpc_response["result"]
    print(f"    ├── 請求回應狀態: {conn_result.get('status')}")
    print(f"    └── 伺服器訊息  : {conn_result.get('message')}")

    if conn_result.get("status") != "ACCEPTED":
        print("[!] 請求遭核心伺服器拒絕，無法建立 Socket 連線。")
        return

    # --------------------------------------------------------
    # 步驟 3：輪詢 get_assigned_port 取得分配到的 port
    #
    #   時序：
    #     rpc_queue → main_server (PortPool.allocate) → rpc_notify_queue
    #     → rpc_server._notify_listener 寫入 _port_map
    #     → get_assigned_port 回 READY
    #     → 同時 socket_cmd_queue → socket_server bind() 就緒
    # --------------------------------------------------------
    print(f"\n[步驟 3] 請求已受理，輪詢 get_assigned_port 等待 port 分配...")
    print(f"    (最長等待 {PORT_POLL_TIMEOUT}s，每 {PORT_POLL_INTERVAL}s 輪詢一次)")

    assigned_port = poll_for_assigned_port(CLIENT_ID)

    if assigned_port is None:
        print(f"[!] 超時：{PORT_POLL_TIMEOUT}s 內未取得 assigned port，中止程序。")
        return

    print(f"    [✓] 取得伺服器分配的 Port: {assigned_port}")

    # 短暫緩衝：確保 socket_server 的執行緒已完成 bind() + listen()
    # _notify_listener 寫入 _port_map 與 socket_server bind() 幾乎同步觸發，
    # 但 bind 有極小的時間差，0.3s 緩衝足夠覆蓋。
    time.sleep(0.3)

    # --------------------------------------------------------
    # 步驟 4：實體 TCP 連線至分配的 port
    # --------------------------------------------------------
    print(f"\n[步驟 4] 開始實體 TCP 連線 ──> 127.0.0.1:{assigned_port}...")

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client_socket.connect(("127.0.0.1", assigned_port))
        print(f"[✓] 成功連入專屬 Socket 通道！(Port: {assigned_port})")

        test_messages = ["Hello Server!", "Heartbeat 01", "Request Data Dump", "exit"]

        for msg in test_messages:
            print(f"\n    [➔] 發送: {msg}")
            client_socket.sendall(f"{msg}\n".encode('utf-8'))

            reply = client_socket.recv(1024).decode('utf-8').strip()
            print(f"    [內網回傳] {reply}")

            if msg.lower() == "exit":
                break

            time.sleep(1.5)
    
    except ConnectionRefusedError:
        print(f"[!] 連線被拒：Port {assigned_port} 尚未就緒，請稍後重試。")
    except Exception as e:
        print(f"[!] Socket 連線或傳輸過程中發生異常: {e}")
    finally:
        client_socket.close()
        print("\n[-] Client 已關閉 Socket 連線。後端資源應會同步被 Main 釋放並回收 Port。")


if __name__ == "__main__":
    main()