import threading
import socket
import time
import requests

_web_report_url = "http://127.0.0.1:8080/api/internal/socket-event"


def _report(event: str, client_id: str = None, extra: dict = None):
    """非同步上報事件至 web_server（fire-and-forget）"""
    payload = {"event": event, "source": "SOCKET", "client_id": client_id}
    if extra:
        payload.update(extra)
    try:
        requests.post(_web_report_url, json=payload, timeout=1)
    except Exception:
        pass


def handle_client_connection(client_data, release_queue):
    """
    動態衍生執行緒：在 main_server 分配的 port 上建立 TCP Server，與 Client 通訊。
    port 由 main_server PortPool 分配，已注入 client_data["port"]。
    """
    thread_id = threading.get_ident()
    client_id = client_data.get("client_id", "unknown_client")
    port      = client_data.get("port")   # 由 main_server 注入，不再有預設值

    if port is None:
        print(f"    [Socket-Thread-{thread_id}] [錯誤] 未收到分配的 port，中止執行緒。")
        release_queue.put({"action": "RELEASE_THREAD", "port": None})
        return

    print(f"    [Socket-Thread-{thread_id}] 執行緒已啟動。在 Port {port} 為 Client [{client_id}] 建立監聽服務...")

    server_socket = None
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("0.0.0.0", port))
        server_socket.listen(1)
        server_socket.settimeout(30.0)

        print(f"    [Socket-Thread-{thread_id}] Port {port} 已就緒，等待 Client 連線 (限時 30 秒)...")

        client_conn, client_addr = server_socket.accept()
        client_ip   = client_addr[0]
        client_port = client_addr[1]

        print(f"    [Socket-Thread-{thread_id}] 連線建立！Client 來源: {client_addr}")

        _report("connect", client_id=client_id, extra={
            "ip":   client_ip,
            "port": client_port,
            "time": time.strftime("%H:%M:%S"),
        })

        client_conn.settimeout(None)

        while True:
            data = client_conn.recv(1024)
            if not data:
                print(f"    [Socket-Thread-{thread_id}] Client 主動中斷連線。")
                _report("disconnect", client_id=client_id, extra={"reason": "client_closed", "port": port})
                break

            msg = data.decode('utf-8').strip()
            print(f"    [Socket-Thread-{thread_id}] 收到 [{client_id}]: {msg}")

            _report("message", client_id=client_id, extra={
                "message": msg,
                "recv":    len(data),
                "sent":    0,
            })

            response = f"Server Echo: {msg}\n"
            client_conn.sendall(response.encode('utf-8'))

            _report("message", client_id=client_id, extra={
                "message": f"Echo → {msg}",
                "recv":    0,
                "sent":    len(response),
            })

            if msg.lower() == "exit":
                _report("disconnect", client_id=client_id, extra={"reason": "exit_command", "port": port})
                break

        client_conn.close()
        print(f"    [Socket-Thread-{thread_id}] Client 通訊結束，連線已關閉。")

    except socket.timeout:
        print(f"    [Socket-Thread-{thread_id}] [警告] 等待 Client 連線超時，自動關閉 Port {port}。")
        _report("disconnect", client_id=client_id, extra={"reason": "timeout", "port": port})

    except Exception as e:
        print(f"    [Socket-Thread-{thread_id}] 服務異常: {e}")
        _report("error", client_id=client_id, extra={"message": str(e), "port": port})

    finally:
        if server_socket:
            try:
                server_socket.close()
            except Exception:
                pass
        print(f"    [Socket-Thread-{thread_id}] Port {port} 監聽服務已卸載。")

        # 【關鍵】回報 port 號，讓 main_server 可以將 port 歸還給 PortPool
        release_queue.put({"action": "RELEASE_THREAD", "port": port})


def run_socket_server(args, socket_cmd_queue, release_queue):
    """唯一常駐的 Socket 進程入口"""
    global _web_report_url

    web_host = args.host if args else "127.0.0.1"
    web_port = args.web_port if args else 8080
    _web_report_url = f"http://{web_host}:{web_port}/api/internal/socket-event"

    print(f"[*] Socket 常駐進程已啟動，開始監聽主程式派發管道...")
    
    while True:
        try:
            cmd = socket_cmd_queue.get()

            if cmd.get("action") == "SPAWN_THREAD":
                client_data = cmd.get("data")
                t = threading.Thread(
                    target=handle_client_connection,
                    args=(client_data, release_queue),
                    daemon=True
                )
                t.start()
                print(f"[*] Socket 進程衍生新執行緒 ({t.name})，Port {client_data.get('port')}。")

        except KeyboardInterrupt:
            print("[-] Socket 常駐進程接收到關閉訊號，結束運作。")
            break
        except Exception as e:
            print(f"[!] Socket 進程內部異常: {e}")
            time.sleep(1)