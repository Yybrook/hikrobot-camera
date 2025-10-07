import os
import inspect
import typing
import subprocess
import platform
import shutil
import socket


def is_win():
    """
    如果是windows系统，返回True，其他返回false
    :return:
    """
    system = platform.system().lower()
    if "windows" in system:
        return True
    else:
        return False


def int_2_ip(i: int) -> str:
    """
    Convert 32-bit integer to IP address
    :param i:
    :return:
    """
    ip = f"{(i & 0xff000000) >> 24}.{(i & 0x00ff0000) >> 16}.{(i & 0x0000ff00) >> 8}.{i & 0x000000ff}"
    return ip


def ip_2_int(ip: str) -> int:
    """
    Convert IP address to 32-bit integer
    :param ip: "192.168.1.1"
    :return:
    """
    return sum([int(s) << shift for s, shift in zip(ip.split("."), [24, 16, 8, 0])])


def get_host_ip(target_ip: str) -> str:
    """
    Returns the IP address of the network interface
    That is used to connect to the camera with the given IP address.
    :param target_ip: IP address of the camera.
    :return: IP address of the network interface that is used to connect to the camera.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((target_ip, 80))
    host_ip, _ = sock.getsockname()
    sock.close()
    return host_ip


def ping_ip(ip: str, host_ip: str = None, times: int = 3, timeout: int = 1) -> bool:
    if shutil.which("ping") is None:
        raise OSError("ping command not found")

    system = platform.system().lower()
    # --- Windows ---
    if "windows" in system:
        # /n 1 -> 发一次，/w timeout(ms)
        cmd = ["ping", "-n", str(times), "-w", str(timeout * 1000)]
        if host_ip:
            cmd += ["-S", host_ip]
        cmd.append(ip)
    # --- Linux / macOS ---
    else:
        # -c 1 -> 发一次，-W timeout(s)，-I 指定源IP
        cmd = ["ping", "-c", str(times), "-W", str(timeout)]
        if host_ip:
            cmd += ["-I", host_ip]
        cmd.append(ip)

    # 调用 subprocess
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # timeout=timeout + 3,  # 稍微比 ping 超时长一点
    )
    return result.returncode == 0


class CallContext(typing.NamedTuple):
    filename: str
    cls_name: typing.Optional[str]
    func_name: str
    lineno: int

    def __str__(self):
        """格式化显示：ClassName.func_name (file.py:line)"""
        if self.cls_name:
            return f"{self.cls_name}.{self.func_name} ({self.filename}:{self.lineno})"
        return f"{self.func_name} ({self.filename}:{self.lineno})"


def get_call_context(depth: int = 1) -> CallContext:
    """
    获取当前或上几层调用的上下文信息，包括：
      - 文件名
      - 类名（如果存在）
      - 函数名
      - 行号

    :param depth: 0 表示当前函数，1 表示上一级调用者，依此类推。
    :return: CallContext 对象
    """
    try:
        frame = inspect.currentframe()
        for _ in range(depth):
            if frame is None or frame.f_back is None:
                break
            frame = frame.f_back

        if frame is None:
            return CallContext("<unknown>", None, "<unknown>", -1)

        code = frame.f_code
        func_name = code.co_name or "<unknown>"
        filename = os.path.basename(code.co_filename)
        lineno = frame.f_lineno

        locals_dict = frame.f_locals
        cls_name = None
        # 尝试推断类名
        if "self" in locals_dict:
            cls_name = locals_dict["self"].__class__.__name__
        elif "cls" in locals_dict:
            cls_name = locals_dict["cls"].__name__

        if func_name == "<module>":
            func_name = "module"

        return CallContext(filename, cls_name, func_name, lineno)

    finally:
        del frame


if __name__ == "__main__":
    print(ping_ip("169.254.151.1"))
