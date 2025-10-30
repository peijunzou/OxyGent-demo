import socket
import psutil
import time
import logging
from typing import Optional, List

class PortManager:
    """
    端口管理工具类
    提供端口占用检测、进程终止等功能
    """
    
    def __init__(self):
        """初始化端口管理器"""
        self.logger = self._setup_logger()
    
    def _setup_logger(self) -> logging.Logger:
        """设置日志记录器"""
        logger = logging.getLogger('PortManager')
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        
        return logger
    
    def is_port_occupied(self, port: int = 8080) -> bool:
        """
        检测指定端口是否被占用
        
        Args:
            port (int): 要检测的端口号，默认8080
            
        Returns:
            bool: True表示端口被占用，False表示端口空闲
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex(('localhost', port))
                return result == 0
        except Exception as e:
            self.logger.error(f"检测端口{port}时发生错误: {e}")
            return False
    
    def get_processes_using_port(self, port: int = 8080) -> List[psutil.Process]:
        """
        获取占用指定端口的进程列表
        
        Args:
            port (int): 要检查的端口号，默认8080
            
        Returns:
            List[psutil.Process]: 占用该端口的进程列表
        """
        processes = []
        
        try:
            for proc in psutil.process_iter(['pid', 'name', 'connections']):
                try:
                    connections = proc.info['connections']
                    if connections:
                        for conn in connections:
                            if (conn.laddr.port == port and 
                                conn.status == psutil.CONN_LISTEN):
                                processes.append(proc)
                                break
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                except Exception as e:
                    self.logger.warning(f"检查进程时发生错误: {e}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"获取进程列表时发生错误: {e}")
            
        return processes
    
    def kill_processes_on_port(self, port: int = 8080) -> bool:
        """
        终止占用指定端口的所有进程
        
        Args:
            port (int): 要清理的端口号，默认8080
            
        Returns:
            bool: True表示成功终止所有进程，False表示部分或全部失败
        """
        processes = self.get_processes_using_port(port)
        
        if not processes:
            self.logger.info(f"端口{port}未被任何进程占用")
            return True
        
        success_count = 0
        total_count = len(processes)
        
        for proc in processes:
            try:
                pid = proc.pid
                name = proc.name()
                self.logger.info(f"正在终止进程: PID={pid}, Name={name}")
                
                # 先尝试优雅终止
                proc.terminate()
                
                # 等待进程终止
                try:
                    proc.wait(timeout=3)
                    self.logger.info(f"进程 {pid} ({name}) 已成功终止")
                    success_count += 1
                except psutil.TimeoutExpired:
                    # 如果优雅终止失败，强制终止
                    self.logger.warning(f"进程 {pid} ({name}) 未响应terminate，尝试强制终止")
                    proc.kill()
                    proc.wait(timeout=2)
                    self.logger.info(f"进程 {pid} ({name}) 已强制终止")
                    success_count += 1
                    
            except psutil.NoSuchProcess:
                self.logger.info(f"进程已不存在，可能已自行退出")
                success_count += 1
            except psutil.AccessDenied:
                self.logger.error(f"权限不足，无法终止进程 {proc.pid}")
            except Exception as e:
                self.logger.error(f"终止进程 {proc.pid} 时发生错误: {e}")
        
        return success_count == total_count
    
    def ensure_port_available(self, port: int = 8080, max_retries: int = 3) -> bool:
        """
        确保指定端口可用（公共方法）
        如果端口被占用，则终止占用的进程，等待5秒后返回结果
        
        Args:
            port (int): 要检查的端口号，默认8080
            max_retries (int): 最大重试次数，默认3次
            
        Returns:
            bool: True表示端口现在可用，False表示清理失败
        """
        self.logger.info(f"开始检查端口 {port} 的可用性")
        
        for attempt in range(max_retries):
            if not self.is_port_occupied(port):
                self.logger.info(f"端口 {port} 当前可用")
                return True
            
            self.logger.warning(f"端口 {port} 被占用，尝试清理 (第{attempt + 1}次)")
            
            # 终止占用端口的进程
            if self.kill_processes_on_port(port):
                self.logger.info("成功终止所有占用进程，等待5秒...")
                time.sleep(5)
                
                # 再次检查端口状态
                if not self.is_port_occupied(port):
                    self.logger.info(f"端口 {port} 现在可用")
                    return True
                else:
                    self.logger.warning(f"端口 {port} 仍被占用，可能有新进程占用了该端口")
            else:
                self.logger.error("部分进程终止失败")
                if attempt < max_retries - 1:
                    self.logger.info("等待5秒后重试...")
                    time.sleep(5)
        
        self.logger.error(f"经过 {max_retries} 次尝试，仍无法清理端口 {port}")
        return False
    
    def get_port_info(self, port: int = 8080) -> dict:
        """
        获取端口的详细信息
        
        Args:
            port (int): 要查询的端口号，默认8080
            
        Returns:
            dict: 包含端口状态和占用进程信息的字典
        """
        info = {
            'port': port,
            'occupied': False,
            'processes': []
        }
        
        info['occupied'] = self.is_port_occupied(port)
        
        if info['occupied']:
            processes = self.get_processes_using_port(port)
            for proc in processes:
                try:
                    proc_info = {
                        'pid': proc.pid,
                        'name': proc.name(),
                        'cmdline': ' '.join(proc.cmdline()),
                        'create_time': proc.create_time()
                    }
                    info['processes'].append(proc_info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        
        return info


def main():
    """示例用法"""
    port_manager = PortManager()
    
    # 检查8080端口状态
    print("=== 端口状态检查 ===")
    port_info = port_manager.get_port_info(8080)
    print(f"端口 {port_info['port']} 占用状态: {port_info['occupied']}")
    
    if port_info['occupied']:
        print("占用进程:")
        for proc in port_info['processes']:
            print(f"  PID: {proc['pid']}, Name: {proc['name']}")
    
    # 确保端口可用
    print("\n=== 确保端口可用 ===")
    success = port_manager.ensure_port_available(8080)
    print(f"端口清理结果: {'成功' if success else '失败'}")


if __name__ == "__main__":
    main()