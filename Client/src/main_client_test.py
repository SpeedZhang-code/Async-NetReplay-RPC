import json
import socket
import time
import urllib.request

# ==========================================
# 系統組態設定
# ==========================================
RPC_URL = "http://127.0.0.1:5000/api/rpc"
CLIENT_ID = "Client_Dynamic_9527"
REQUESTED_PORT = 5555

def send_json_rpc(method, params=None):
    """
    輔助函式：透過 HTTP POST 發送標準 JSON-RPC 2.0 請求給 RPC_server
    """
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": int(time.time() * 1000)
    }
    
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(
        RPC_URL, 
        data=json.dumps(payload).encode('utf-8'), 
        headers=headers, 
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"[!] 無法與 RPC_Server 進行通訊: {e}")
        return None

# ==========================================
# Client 主邏輯流程
# ==========================================
def main():
    print(f"[*] {CLIENT_ID} 啟動，準備與後端系統進行握手...")

    # --------------------------------------------------------
    # 邏輯 1：初次溝通 - 服務發現 (Discover)
    # --------------------------------------------------------
    print("\n[步驟 1] 發送 discover 請求查詢伺服器可用方法與執行緒額度...")
    rpc_response = send_json_rpc("discover")
    
    if not rpc_response or "result" not in rpc_response:
        print("[!] 服務發現失敗，中止程序。")
        return
        
    result = rpc_response["result"]
    print(f"    ├── 可用方法群 : {result.get('available_methods')}")
    print(f"    ├── 總執行緒上限: {result.get('socket_thread_limit')} Threads")
    print(f"    ├── 目前已用額度: {result.get('current_active_threads')} Threads")
    print(f"    └── 伺服器狀態 : {result.get('status')}")
    
    if result.get("status") == "FULL":
        print("[!] 伺服器 Socket 額度已滿！無法請求開啟服務，通訊終止。")
        return

    # 隨意停留 1 秒模擬人類操作延遲
    time.sleep(1)
    
    # --------------------------------------------------------
    # 邏輯 2：確定連線 - 呼叫 Method 通知後端衍生進程
    # --------------------------------------------------------
    print(f"\n[步驟 2] 額度充足，呼叫 open_socket_service 請求在 Port {REQUESTED_PORT} 開啟通道...")
    connection_params = {
        "client_id": CLIENT_ID,
        "requested_port": REQUESTED_PORT,
        "timestamp": time.time()
    }
    
    rpc_response = send_json_rpc("open_socket_service", connection_params)
    
    if not rpc_response or "result" not in rpc_response:
        print("[!] 請求開啟 Socket 服務失敗，中止程序。")
        return
        
    conn_result = rpc_response["result"]
    print(f"    ├── 請求回應狀態: {conn_result.get('status')}")
    print(f"    └── 伺服器訊息  : {conn_result.get('message')}")
    
    if conn_result.get("status") != "ACCEPTED":
        print("[!] 請求遭核心伺服器拒絕，無法建立 Socket 連線。")
        return
    
    # 關鍵緩衝：給予後端一點時間。
    # 因為 RPC_server 是非阻塞的（發完 Queue 給 Main 就立刻回覆我們 ACCEPTED 了）
    # 此時 Main 正在發信給 Socket_server，Socket_server 的 Thread 正在拉起 bind()。
    # 我們稍等 0.5 秒再實體連線是最穩健的。
    print("[*] 正在等待後端進程動態衍生監聽執行緒 (預留 0.5 秒緩衝)...")
    time.sleep(0.5)

    # --------------------------------------------------------
    # 邏輯 3：實體連線 - Client 正式連入專屬 TCP Socket Port
    # --------------------------------------------------------
    target_port = conn_result.get("assigned_port", REQUESTED_PORT)
    print(f"\n[步驟 3] 開始實體 TCP 連線 ──> 127.0.0.1:{target_port}...")
    
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client_socket.connect(("127.0.0.1", target_port))
        print(f"[✓] 成功連入專屬 Socket 通道！(Port: {target_port})")
        
        # 進行簡單的資料互動測試
        test_messages = ["Hello Server!", "Heartbeat 01", "Request Data Dump", "exit"]
        
        for msg in test_messages:
            print(f"    [➔] 發送: {msg}")
            client_socket.sendall(f"{msg}\n".encode('utf-8'))
            
            # 接收伺服器 Echo 回傳的資料
            reply = client_socket.recv(1024).decode('utf-8').strip()
            print(f"    [內網回傳] {reply}")
            
            time.sleep(1.5) # 每條訊息間隔 1.5 秒
            
    except Exception as e:
        print(f"[!] Socket 連線或傳輸過程中發生異常: {e}")
    finally:
        client_socket.close()
        print("\n[-] Client 已關閉 Socket 連線。後端資源應會同步被 Main 釋放。")

if __name__ == "__main__":
    main()