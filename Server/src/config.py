import sys
import argparse
import threading


# ==========================================
# 命令列參數解析
# ==========================================
def parse_arguments():
    """解析命令列參數"""
    parser = argparse.ArgumentParser(description="角色特化型隨選伺服器架構")
    parser.add_argument('--host',        type=str, default='127.0.0.1', help='伺服器監聽 IP')
    parser.add_argument('--rpc-port',    type=int, default=5000,        help='RPC 服務埠號')
    parser.add_argument('--web-port',    type=int, default=8080,        help='Flask 網頁埠號')
    parser.add_argument('--socket-port', type=int, default=9090,        help='Socket 服務起始埠號（往上遞增分配）')

    parser.add_argument('--thread-num',  type=int, default=12, help='系統 Thread 總上限')
    parser.add_argument('--process-num', type=int, default=4,  help='Worker Process 總上限')

    if 'ipykernel_launcher' in sys.argv[0]:
        return parser.parse_args(args=[])

    return parser.parse_args()


# ==========================================
# 資源分配演算法
# ==========================================
def allocate_resources(args):
    """
    Socket 單進程 + 平均分配型演算法
    - 固定進程：Flask(1)、RPC(1)、Socket(1)
    - 優先滿足 Flask Web 執行緒（25%，最少 4 個）
    - 扣除 RPC 固定開銷：1 個 werkzeug 載體 + 1 個核心控制執行緒（共 2 個）
    - 剩餘的執行緒額度，由 RPC 工作池與 Socket 工作池「平均分配」
    """

    rpc_proc = 1
    web_proc = 1
    socket_proc = 1
    
    web_threads = max(4, args.thread_num // 4) 

    rpc_werkzeug_threads = 1
    rpc_control_threads = 1
    rpc_fixed_overhead = rpc_werkzeug_threads + rpc_control_threads  
    
    # 計算剩下真正可用來「平均分配」的工作執行緒總量
    total_remaining_threads = args.thread_num - web_threads - rpc_fixed_overhead
    
    # 將剩下的額度平均分配給 RPC 工作池與 Socket 工作池（最少給 1 個）
    shared_threads = max(1, total_remaining_threads // 2)
    
    # 如果除不盡，將餘數補給 Socket 工作池
    remainder = max(0, total_remaining_threads % 2)
    
    rpc_worker_threads = shared_threads
    socket_worker_threads = shared_threads + remainder
    
    # 封裝
    allocation = {
        "rpc": {
            "procs": rpc_proc,
            "werkzeug_threads": rpc_werkzeug_threads,
            "control_threads": rpc_control_threads,
            "worker_pool_threads": rpc_worker_threads  
        },
        "web": {
            "procs": web_proc,
            "threads": web_threads                           
        },
        "socket_pool": {
            "procs": socket_proc,
            "threads": socket_worker_threads               
        }
    }
    return allocation


# ==========================================
# Port Pool（執行緒安全）
# ==========================================
class PortPool:
    """
    管理 Socket 服務專用的動態 Port 池。

    - 啟動時預先產生 `capacity` 個可用 port（base_port, base_port+1, ...）
    - allocate() 取出一個可用 port；池空時回傳 None
    - release(port) 歸還 port 供下一次重用
    - 執行緒安全（Lock 保護）
    """
    
    def __init__(self, base_port: int, capacity: int):
        self._lock      = threading.Lock()
        self._available = list(range(base_port, base_port + capacity))
        self._in_use: set[int] = set()
        self.base_port  = base_port
        self.capacity   = capacity

    def allocate(self) -> int | None:
        """取得一個可用 port；若池已耗盡回傳 None。"""
        with self._lock:
            if not self._available:
                return None
            port = self._available.pop(0)
            self._in_use.add(port)
            return port

    def release(self, port: int):
        """歸還 port 回到可用清單尾端。"""
        with self._lock:
            if port in self._in_use:
                self._in_use.discard(port)
                self._available.append(port)

    def status(self) -> dict:
        """回傳目前池狀態（供 debug / 監控用）。"""
        with self._lock:
            return {
                "available": list(self._available),
                "in_use":    list(self._in_use),
                "capacity":  self.capacity,
            }

    def __repr__(self):
        s = self.status()
        return (f"<PortPool base={self.base_port} capacity={self.capacity} "
                f"available={len(s['available'])} in_use={len(s['in_use'])}>")


# ==========================================
# 全域單例：由 main_server 初始化後，其他模組 import 使用
# ==========================================
_port_pool: PortPool | None = None

def init_port_pool(args, allocation) -> PortPool:
    """
    由 main_server 在啟動時呼叫一次，建立全域 PortPool 單例。
    capacity = socket_pool threads 上限（即全系統最大同時連線數）
    """
    global _port_pool
    capacity   = allocation['socket_pool']['threads']
    _port_pool = PortPool(base_port=args.socket_port, capacity=capacity)
    print(f"[*] PortPool 初始化完成：{_port_pool}")
    return _port_pool

def get_port_pool() -> PortPool:
    """取得全域 PortPool 單例（需先呼叫 init_port_pool）。"""
    if _port_pool is None:
        raise RuntimeError("PortPool 尚未初始化，請先呼叫 init_port_pool()。")
    return _port_pool