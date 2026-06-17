import multiprocessing
import os
import sys
from multiprocessing.connection import wait

from rpc_server import init_rpc_module
from web_server import run_flask_server
from socket_server import run_socket_server
from config import parse_arguments, allocate_resources, init_port_pool


def main():
    args = parse_arguments()
    print(f"[*] 主伺服器啟動 (PID: {os.getpid()})")

    allocation = allocate_resources(args)

    print(f"[*] 特化資源分配結果 (已強化 Flask Thread 且 RPC 控制調為 {allocation['rpc']['control_threads']}):")
    print(f"    - Flask 網頁服務 : {allocation['web']['procs']} Process / {allocation['web']['threads']} Thread")
    print(f"    - RPC 服務   : {allocation['rpc']['procs']} Process 內部分配：")
    print(f"       ├── werkzeug: 1 Thread (RPC的載體)")
    print(f"       ├── 核心控制執行緒 : {allocation['rpc']['control_threads']} Thread")
    print(f"       └── 預留Socket額度 : {allocation['rpc']['worker_pool_threads']} Threads")
    print(f"    - Socket 進程池上限 : {allocation['socket_pool']['procs']} Process / {allocation['socket_pool']['threads']} Threads")

    # ── 初始化 PortPool ──
    port_pool = init_port_pool(args, allocation)

    core_processes = []

    rpc_queue        = multiprocessing.Queue()
    web_queue        = multiprocessing.Queue()
    release_queue    = multiprocessing.Queue()
    socket_cmd_queue = multiprocessing.Queue()
    rpc_notify_queue = multiprocessing.Queue()
    
    max_socket_threads     = allocation['socket_pool']['threads']
    current_active_threads = 0

    try:
        rpc_process = multiprocessing.Process(
            target=init_rpc_module,
            args=(args, allocation['rpc'], rpc_queue, rpc_notify_queue),
            name="RPC_Server_Process"
        )
        rpc_process.start()
        core_processes.append(rpc_process)

        flask_process = multiprocessing.Process(
            target=run_flask_server,
            args=(args, allocation['web']['threads'], web_queue),
            name="Flask_Server_Process"
        )
        flask_process.start()
        core_processes.append(flask_process)

        socket_process = multiprocessing.Process(
            target=run_socket_server,
            args=(args, socket_cmd_queue, release_queue),
            name="Socket_Server_Process_1"
        )
        socket_process.start()
        core_processes.append(socket_process)

        print(f"[✓] 核心常駐進程群已啟動。固定 Socket 進程 PID: {socket_process.pid}")
        print("[*] 主行程已進入多管道事件監聽狀態 (零 CPU 消耗)...")

        readers = [rpc_queue._reader, web_queue._reader, release_queue._reader]

        while True:
            ready_readers = wait(readers)

            for reader in ready_readers:

                # 1. Socket 執行緒釋放通知（含 port 回收）
                if reader is release_queue._reader:
                    try:
                        release_msg = release_queue.get_nowait()
                        if release_msg.get("action") == "RELEASE_THREAD":
                            current_active_threads = max(0, current_active_threads - 1)
                            freed_port = release_msg.get("port")
                            if freed_port:
                                port_pool.release(freed_port)
                                print(f"[-] Client 斷開。釋放 Port {freed_port} 回池。"
                                      f"(負載: {current_active_threads}/{max_socket_threads})")
                            else:
                                print(f"[-] Client 斷開。(負載: {current_active_threads}/{max_socket_threads})")
                    except Exception:
                        pass

                # 2. RPC 傳來的連線要求
                elif reader is rpc_queue._reader:
                    try:
                        rpc_msg = rpc_queue.get_nowait()
                        client_id = rpc_msg.get("client_id", "unknown")
                        print(f"\n[➔] RPC 接收到開啟連線要求。(負載: {current_active_threads}/{max_socket_threads})")

                        if current_active_threads >= max_socket_threads:
                            print(f"[!] 拒絕 RPC 請求：已達全系統 Socket 執行緒上限！")
                            rpc_notify_queue.put({"status": "REJECTED", "client_id": client_id, "port": None})
                            continue

                        assigned_port = port_pool.allocate()
                        if assigned_port is None:
                            print(f"[!] 拒絕 RPC 請求：PortPool 已耗盡！")
                            rpc_notify_queue.put({"status": "REJECTED", "client_id": client_id, "port": None})
                            continue

                        rpc_msg["port"] = assigned_port

                        if socket_process.is_alive():
                            socket_cmd_queue.put({"action": "SPAWN_THREAD", "source": "RPC", "data": rpc_msg})
                            current_active_threads += 1

                            # ── 核心：通知 rpc_server 已分配的 port，讓它上報 web_server ──
                            rpc_notify_queue.put({"status": "ACCEPTED", "client_id": client_id, "port": assigned_port})

                            print(f"[✓] RPC 連線任務 → Socket 進程，指派 Port {assigned_port}。"
                                  f"負載: {current_active_threads}/{max_socket_threads}")
                        else:
                            port_pool.release(assigned_port)
                            rpc_notify_queue.put({"status": "REJECTED", "client_id": client_id, "port": None})
                            print(f"[!] 錯誤：Socket 常駐進程已死亡！")
                    except Exception as e:
                        print(f"[!] RPC 調度異常: {e}")

                # 3. WEB 傳來的連線要求
                elif reader is web_queue._reader:
                    try:
                        web_msg = web_queue.get_nowait()
                        print(f"\n[➔] WEB 接收到開啟連線要求。(負載: {current_active_threads}/{max_socket_threads})")

                        if current_active_threads >= max_socket_threads:
                            print(f"[!] 拒絕 WEB 請求：已達全系統 Socket 執行緒上限！")
                            continue

                        assigned_port = port_pool.allocate()
                        if assigned_port is None:
                            print(f"[!] 拒絕 WEB 請求：PortPool 已耗盡！")
                            continue

                        web_msg["port"] = assigned_port

                        if socket_process.is_alive():
                            socket_cmd_queue.put({"action": "SPAWN_THREAD", "source": "WEB", "data": web_msg})
                            current_active_threads += 1
                            print(f"[✓] WEB 連線任務 → Socket 進程，指派 Port {assigned_port}。"
                                  f"負載: {current_active_threads}/{max_socket_threads}")
                        else:
                            port_pool.release(assigned_port)
                            print(f"[!] 錯誤：Socket 常駐進程已死亡！")
                    except Exception as e:
                        print(f"[!] WEB 調度異常: {e}")

    except KeyboardInterrupt:
        print("\n[-] 接收到主程式結束訊號，正在安全關閉所有常駐子進程...")
        for p in core_processes:
            if p.is_alive():
                p.terminate()
                p.join()
        print("[✓] 所有進程已關閉，伺服器安全登出。")
        sys.exit(0)


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()