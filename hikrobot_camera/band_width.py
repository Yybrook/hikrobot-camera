import psutil
import time
from datetime import datetime
import curses
import traceback


def get_network_data():
    """
    获取网卡流量信息
    :return:
    """
    # 存储接收和发送字节数
    recv = dict()
    sent = dict()
    # 获取每个网卡的流量信息, 单独统计
    data = psutil.net_io_counters(pernic=True)
    # 获取所有网卡的名字
    interfaces = data.keys()
    for interface in interfaces:
        recv.setdefault(interface, data.get(interface).bytes_recv)  # 获取接收的字节数
        sent.setdefault(interface, data.get(interface).bytes_sent)  # 获取发送的字节数
    return interfaces, recv, sent


def get_network_rate(num_sec):
    """
    计算网卡流量速率
    :param num_sec:
    :return:
    """
    # 获取当前的流量数据
    interfaces, old_recv, old_sent = get_network_data()
    # 等待 num 秒
    time.sleep(num_sec)
    # 获取 num 秒后新的流量数据
    interfaces, new_recv, new_sent = get_network_data()


    # 存储计算后的速率
    network_in = dict()
    network_out = dict()
    # 计算速率
    for interface in interfaces:
        network_in.setdefault(
            interface,
            float("%.3f" % ((new_recv.get(interface) - old_recv.get(interface)) / num_sec)),
        )
        network_out.setdefault(
            interface,
            float("%.3f" % ((new_sent.get(interface) - old_sent.get(interface)) / num_sec)),
        )
    return interfaces, network_in, network_out


def show(stdscr, curr_time, interfaces, network_in, network_out, net_unit):
    row = 1
    col = 6
    # 输出当前时间到终端, 第 0 行第 0 列
    stdscr.addstr(0, 0, datetime.strftime(curr_time, "%Y-%m-%d %H:%M:%S"))
    for interface in interfaces:
        # 排除掉特定的接口
        # lo（本地回环）, veth（Docker / 容器虚拟网卡）, 蓝牙网卡, VMware 虚拟网卡
        if (
            interface.lower().startswith("veth") or
            interface.lower().startswith("蓝牙") or
            interface.lower().startswith("vmware") or
            interface.lower().startswith("vetherne") or
            interface.lower().startswith("lo")
        ):
            continue
        # 根据单位选择显示的格式
        if net_unit == "K" or net_unit == "k":
            net_in = "%12.2fKB/s" % (network_in.get(interface, 0) / 1024)
            net_out = "%11.2fKB/s" % (network_out.get(interface, 0) / 1024)
        elif net_unit == "M" or net_unit == "m":
            net_in = "%12.2fMB/s" % (network_in.get(interface, 0) / 1024 / 1024)
            net_out = "%11.2fMB/s" % (network_out.get(interface, 0) / 1024 / 1024)
        elif net_unit == "G" or net_unit == "g":
            net_in = "%12.3fGB/s" % (network_in.get(interface, 0) / 1024 / 1024 / 1024)
            net_out = "%11.3fGB/s" % (network_out.get(interface, 0) / 1024 / 1024 / 1024)
        else:
            net_in = "%12.1fB/s" % network_in.get(interface, 0)
            net_out = "%11.1fB/s" % network_out.get(interface, 0)
        stdscr.addstr(row, col, interface)
        stdscr.addstr(row + 1, col, "Input:%s" % net_in)
        stdscr.addstr(row + 2, col, "Output:%s" % net_out)
        stdscr.move(row + 3, col)
        row += 4
        stdscr.refresh()


def output(num_sec, net_unit):
    """
    在终端中动态显示网卡速率
    :param num_sec:
    :param net_unit:
    :return:
    """
    interfaces, _, _ = get_network_data()
    print("interfaces: ", list(interfaces))

    # 初始化 curses 窗口, 进入“全屏模式”
    stdscr = curses.initscr()
    # 启用颜色
    curses.start_color()
    # 输入时不回显
    curses.noecho()
    # 启用“立即响应模式”
    curses.cbreak()
    # 清空屏幕
    stdscr.clear()

    col = 6

    try:
        # 第一次初始化
        # 获取网卡信息
        interfaces, _, _ = get_network_data()
        # 获取当前时间
        curr_time = datetime.now()
        # 显示
        show(stdscr, curr_time, interfaces, dict(), dict(), net_unit)

        # 第二次开始循环监控网卡流量
        while True:
            # 获取网卡速率
            _, network_in, network_out = get_network_rate(num_sec)
            curr_time = datetime.now()
            # 擦除
            stdscr.erase()
            # 显示
            show(stdscr, curr_time, interfaces, network_in, network_out, net_unit)

    except KeyboardInterrupt:
        pass
    except Exception as err:
        traceback.print_exc()
        print("Please increase the terminal size!")
    finally:
        curses.echo()
        curses.nocbreak()
        curses.endwin()


def version():
    return "0.1"
