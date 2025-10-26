import sys
import os
import subprocess
import typing
import ctypes
import enum
import pandas as pd
import numpy as np
import cv2
import yaml
import logging
import dataclasses
from threading import Lock

from .multi_hikrobot_cameras import MultiHikrobotCameras
from .hik_error_map import HikErrorMap
from . import utils

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 导入 MvCameraControl_class 包
# --------------------------------------------------------------------------- #
# 1. 官方导包方式
# get MVS SDK path
# sys.path.append(os.getenv('MVCAM_COMMON_RUNENV') + "/Samples/Python/MvImport")
# from MvCameraControl_class import *

# 2. 自定义导包方式
# get MVS SDK path for windows and linux
if utils.is_win():
    # Hikrobot MVS SDK Location on Windows systems
    MVCAM_COMMON_RUNENV_PATH = os.getenv('MVCAM_COMMON_RUNENV', r"C:\Program Files (x86)\MVS\Development")
    MVIMPORT_DIR = os.path.join(MVCAM_COMMON_RUNENV_PATH, r"Samples\Python\MvImport")
else:
    # todo 需要验证
    # Hikrobot MVS SDK Location on UNIX systems
    MVCAM_SDK_PATH = os.environ.get("MVCAM_SDK_PATH", "/opt/MVS")
    MVIMPORT_DIR = os.path.join(MVCAM_SDK_PATH, "Samples/64/Python/MvImport")
# 导入sdk模块
try:
    # 临时将 MvImport 目录加入 sys.path
    if MVIMPORT_DIR not in sys.path:
        sys.path.insert(0, MVIMPORT_DIR)
    import MvCameraControl_class as HIK
except ModuleNotFoundError as err:
    _logger.exception(f"can't find MvCameraControl_class.py in [{MVIMPORT_DIR}], please install MVS SDK.")
    raise ModuleNotFoundError(f"can't find MvCameraControl_class.py in [{MVIMPORT_DIR}]") from err
finally:
    # 清理 sys.path，防止污染全局环境
    if MVIMPORT_DIR in sys.path:
        sys.path.remove(MVIMPORT_DIR)

class Rotation(enum.IntEnum):
    """
    图像旋转标记
    对应 OpenCV 的旋转常量：
      - ROTATE_90_CLOCKWISE        (0): 顺时针 90°
      - ROTATE_180                 (1): 顺时针 180°
      - ROTATE_90_COUNTERCLOCKWISE (2): 逆时针 90°
      - NONE                       (3): 不旋转
    """
    CW90 = cv2.ROTATE_90_CLOCKWISE
    CW180 = cv2.ROTATE_180
    CCW90 = cv2.ROTATE_90_COUNTERCLOCKWISE
    NONE = 3

    def is_90(self) -> bool:
        """
        是否为 90° 旋转（顺时针或逆时针）
        :return:
        """
        return self in (Rotation.CW90, Rotation.CCW90)

    def is_rotate(self) -> bool:
        """
        是否旋转
        :return:
        """
        return self in (Rotation.CW90, Rotation.CCW90, Rotation.CW180)

class GrabMethod(enum.IntEnum):
    """相机取流方式"""
    GetOneFrameTimeout = 1
    GetImageBuffer = 2
    RegisterImageCallBackEx = 3

class AccessMode(enum.IntEnum):
    """设备访问模式"""
    # 1 独占权限，其他APP只允许读CCP寄存器
    Exclusive = HIK.MV_ACCESS_Exclusive
    # # 2 可以从5模式下抢占权限，然后以独占权限打开
    # ExclusiveWithSwitch = HIK.MV_ACCESS_ExclusiveWithSwitch
    # 3 控制权限，其他APP允许读所有寄存器
    Control = HIK.MV_ACCESS_Control
    # # 4 可以从5的模式下抢占权限，然后以控制权限打开
    # ControlWithSwitch = HIK.MV_ACCESS_ControlWithSwitch
    # # 5 以可被抢占的控制权限打开
    # ControlSwitchEnable = HIK.MV_ACCESS_ControlSwitchEnable
    # # 6 可以从5的模式下抢占权限，然后以可被抢占的控制权限打开
    # ControlSwitchEnableWithKey = HIK.MV_ACCESS_ControlSwitchEnableWithKey
    # 7 读模式打开设备，适用于控制权限下
    Monitor = HIK.MV_ACCESS_Monitor

    def is_exclusive(self) -> bool:
        return self == AccessMode.Exclusive

    def has_control_permission(self) -> bool:
        return self in (AccessMode.Control, AccessMode.Exclusive)

class CreateHandleMethod(enum.IntEnum):
    """
    相机创建句柄方式
    0 -> IP直连相机, 1 -> 枚举相机
    """
    Direct = 0
    Enum = 1

@dataclasses.dataclass
class CameraCustomParams:
    # 相机ip
    _ip: str
    # 主机ip, 为 "" 时自动确定，为 None 时缺省
    host_ip: typing.Optional[str] = None
    # 是否在初始化时ping相机
    to_ping: bool = False
    # 取流方法, 1 -> MV_CC_GetOneFrameTimeout, 2 -> MV_CC_GetImageBuffer, 3 -> MV_CC_RegisterImageCallBackEx
    grab_method: GrabMethod = GrabMethod.GetImageBuffer
    # 访问模式
    access_mode: AccessMode = AccessMode.Exclusive
    # 创建句柄方式, 0 -> 通过ip直连, 1 -> 通过枚举
    create_handle_method: CreateHandleMethod = CreateHandleMethod.Direct
    # resize
    resize_ratio: typing.Optional[float] = None
    # 旋转
    rotation: Rotation = Rotation.NONE
    # 获取一帧的超时时间
    get_one_frame_timeout_ms: int = 1000
    # 组播ip
    multicast_ip: str = None
    # 组播port
    multicast_port: int = 1042

    def __post_init__(self):
        if isinstance(self.access_mode, int):
            self.access_mode = AccessMode(self.access_mode)

        if isinstance(self.grab_method, int):
            self.grab_method = GrabMethod(self.grab_method)

        if isinstance(self.create_handle_method, int):
            self.create_handle_method = CreateHandleMethod(self.create_handle_method)

        if isinstance(self.rotation, int):
            self.rotation = Rotation(self.rotation)

        # 根据 ip 确定 host ip
        if isinstance(self.host_ip, str):
            self.host_ip = self.host_ip.strip()
        if self.host_ip == "":
            self.host_ip = utils.get_host_ip(target_ip=self._ip).strip()

        # 非独占模式下，根据 ip 确定 multicast_ip
        if not self.access_mode.is_exclusive() and self.multicast_ip is None:
            self.multicast_ip = f"239.192.1.{self._ip.split('.')[3]}"


"""
stFrameInfo: MV_FRAME_OUT_INFO_EX
    ('nWidth', c_ushort),               ## @~chinese 图像宽(最大65535，超出请用nExtendWidth)    @~english Image Width (over 65535, use nExtendWidth)
    ('nHeight', c_ushort),              ## @~chinese 图像高(最大65535，超出请用nExtendHeight)   @~english Image Height(over 65535, use nExtendHeight)
    ('enPixelType', MvGvspPixelType),                        ## @~chinese 像素格式           @~english Pixel Type
    ('nFrameNum', c_uint),                                   ## @~chinese 帧号               @~english Frame Number
    ('nDevTimeStampHigh', c_uint),                           ## @~chinese 时间戳高32位       @~english Timestamp high 32 bits
    ('nDevTimeStampLow', c_uint),                            ## @~chinese 时间戳低32位       @~english Timestamp low 32 bits
    ('nReserved0', c_uint),                                  ## @~chinese 保留，8字节对齐     @~english Reserved, 8-byte aligned
    ('nHostTimeStamp', int64_t),                             ## @~chinese 主机生成的时间戳    @~english Host-generated timestamp
    ('nFrameLen', c_uint),                                   ## @~chinese 帧的长度           @~english Frame length
    ## @~chinese 以下为chunk新增水印信息 @~english The followings are chunk add frame-specific information
    ## @~chinese 设备水印时标 @~english Device frame-specific time scale
    ('nSecondCount', c_uint),                                ## @~chinese 秒数               @~english The Seconds                         
    ('nCycleCount', c_uint),                                 ## @~chinese 周期数             @~english The Count of Cycle                
    ('nCycleOffset', c_uint),                                ## @~chinese 周期偏移量         @~english The Offset of Cycle                  
    ('fGain', c_float),                                      ## @~chinese 增益               @~english Gain
    ('fExposureTime', c_float),                              ## @~chinese 曝光时间           @~english Exposure Time
    ('nAverageBrightness', c_uint),                          ## @~chinese 平均亮度           @~english Average brightness
    ## @~chinese:白平衡相关 @~english White balance
    ('nRed', c_uint),                                        ## @~chinese 红色               @~english Red     
    ('nGreen', c_uint),                                      ## @~chinese 绿色               @~english Green
    ('nBlue', c_uint),                                       ## @~chinese 蓝色               @~english Blue
    ('nFrameCounter', c_uint),                               ## @~chinese 帧计数             @~english Frame counter
    ('nTriggerIndex', c_uint),                               ## @~chinese 触发计数           @~english Trigger index
    ## @~chinese  输入/输出 @~english Line Input/Output
    ('nInput', c_uint),                                      ## @~chinese 输入               @~english input
    ('nOutput', c_uint),                                     ## @~chinese 输出               @~english output
    ## @~chinese ROI区域 @~english ROI Region                       
    ('nOffsetX', c_ushort),                                  ## @~chinese 水平偏移量             @~english OffsetX   
    ('nOffsetY', c_ushort),                                  ## @~chinese 垂直偏移量             @~english OffsetY
    ('nChunkWidth', c_ushort),                               ## @~chinese chunk 宽              @~english The Width of Chunk
    ('nChunkHeight', c_ushort),                              ## @~chinese chunk 高               @~english The Height of Chunk
    ('nLostPacket', c_uint),                                 ## @~chinese 本帧丢包数            @~english Lost Pacekt Number In This Frame
    ('nUnparsedChunkNum', c_uint),                           ## @~chinese 未解析的Chunkdata个数 @~english Unparsed chunk number
    ('UnparsedChunkList', N22_MV_FRAME_OUT_INFO_EX_3DOT_1E), ## @~chinese 未解析的Chunk数据      @~english Unparsed chunk list
    ('nExtendWidth', c_uint),                                ## @~chinese 图像宽(扩展变量)       @~english Image Width
    ('nExtendHeight', c_uint),                               ## @~chinese 图像高(扩展变量)       @~english Image Height
    ('nFrameLenEx', uint64_t),                               ## @~chinese 帧的长度               @~english The Length of Frame   
    ('nReserved1', c_uint),                                  ## @~chinese 保留，用于对齐         @~english Reserved
    ('nSubImageNum', c_uint),                                ## @~chinese 图像缓存中的子图个数   @~english  Number of sub-images in the image cache
    ('SubImageList', N22_MV_FRAME_OUT_INFO_EX_3DOT_2E),      ## @~chinese 子图信息               @~english Sub image info
    ('UserPtr', N22_MV_FRAME_OUT_INFO_EX_3DOT_3E),           ## @~chinese 自定义指针(外部注册缓存时，内存地址对应的用户自定义指针)          @~english Custom pointer (user-defined pointer corresponding to memory address when registering external cache)
    ('nReserved', c_uint * 26),                              ## @~chinese 保留字节            @~english Reserved bytes

stOutFrame: MV_FRAME_OUT
    ('pBufAddr', POINTER(c_ubyte)),         ## @~chinese 图像指针地址         @~english pointer of image
    ('stFrameInfo', MV_FRAME_OUT_INFO_EX),  ## @~chinese 图像信息            @~english information of the specific image
    ('nRes', c_uint * 16),                  ## @~chinese 保留字节            @~english Reserved bytes


stDevInfo: MV_CC_DEVICE_INF ->     
    ('nMajorVer', c_ushort),                              ## @~chinese 规范的主要版本         @~english Major version of the specification.
    ('nMinorVer', c_ushort),                              ## @~chinese 规范的次要版本         @~english Minor version of the specification
    ('nMacAddrHigh', c_uint),                             ## @~chinese MAC地址高位            @~english Mac address high
    ('nMacAddrLow', c_uint),                              ## @~chinese MAC地址低位            @~english Mac address low
    ('nTLayerType', c_uint),                              ## @~chinese 设备传输层协议类型     @~english Device Transport Layer Protocol Type, e.g. MV_GIGE_DEVICE
    ('nDevTypeInfo', c_uint),                             ## @~chinese 设备类型信息           @~english Device Type Info
    ('nReserved', c_uint * 3),                            ## @~chinese 保留字节               @~english Reserved bytes
    ('SpecialInfo', N19_MV_CC_DEVICE_INFO_3DOT_0E),       ## @~chinese 不同设备特有信息         @~english Special information
SpecialInfo: N19_MV_CC_DEVICE_INFO_3DOT_0E -> 
    ('stGigEInfo', MV_GIGE_DEVICE_INFO),                   ## @~chinese Gige设备信息        @~english Gige device infomation
    ('stUsb3VInfo', MV_USB3_DEVICE_INFO),                  ## @~chinese U3V设备信息         @~english u3V device information
    ('stCamLInfo', MV_CamL_DEV_INFO),                      ## @~chinese CamLink设备信息     @~english CamLink device information
    ('stCMLInfo', MV_CML_DEVICE_INFO),                     ## @~chinese 采集卡CameraLink设备信息       @~english CameraLink Device Info On Frame Grabber
    ('stCXPInfo', MV_CXP_DEVICE_INFO),                     ## @~chinese 采集卡CoaXPress设备信息        @~english CoaXPress Device Info On Frame Grabber
    ('stXoFInfo', MV_XOF_DEVICE_INFO),                     ## @~chinese 采集卡XoF设备信息              @~english XoF Device Info On Frame Grabber
    ('stVirInfo', MV_GENTL_VIR_DEVICE_INFO),               ## @~chinese 虚拟相机信息                   @~english Virtual device information
stGigEInfo: MV_GIGE_DEVICE_INFO -> 
    ('nIpCfgOption', c_uint),                     ## @~chinese IP配置选项         @~english Ip config option
    ('nIpCfgCurrent', c_uint),                    ## @~chinese 当前IP地址配置     @~english IP configuration:bit31-static bit30-dhcp bit29-lla
    ('nCurrentIp', c_uint),                       ## @~chinese 当前主机IP地址     @~english Current host Ip 
    ('nCurrentSubNetMask', c_uint),               ## @~chinese 当前子网掩码       @~english curtent subnet mask
    ('nDefultGateWay', c_uint),                   ## @~chinese 默认网关           @~english Default gate way
    ('chManufacturerName', c_ubyte * 32),         ## @~chinese 厂商名称           @~english Manufacturer Name
    ('chModelName', c_ubyte * 32),                ## @~chinese 型号名称           @~english Mode name
    ('chDeviceVersion', c_ubyte * 32),            ## @~chinese 设备固件版本       @~english Device Version
    ('chManufacturerSpecificInfo', c_ubyte * 48), ## @~chinese 厂商特殊信息       @~english Manufacturer Specific Infomation
    ('chSerialNumber', c_ubyte * 16),             ## @~chinese 序列号             @~english serial number
    ('chUserDefinedName', c_ubyte * 16),          ## @~chinese 用户定义名称       @~english User Defined Name
    ('nNetExport', c_uint),                       ## @~chinese 网口Ip地址         @~english NetWork Ip address
    ('nReserved', c_uint * 4),                    ## @~chinese 保留字节         @~english Reserved bytes

"""


class HikrobotCamera(HIK.MvCamera):
    # 存放所有相机的枚举信息
    devices_info = dict()

    def __init__(self, **kwargs):
        """
        :param _ip/ip: 相机ip
        :param camera_nodes_path:   相机节点 csv 路径
        :param camera_params_path:  相机参数 yaml 路径
        :param host_ip: 主机ip, 为 "" 时自动确定，为 None 时缺省
        :param grab_method: 取流方法, 1 -> MV_CC_GetOneFrameTimeout, 2 -> MV_CC_GetImageBuffer, 3 -> MV_CC_RegisterImageCallBackEx
        :param access_mode: 访问模式
        :param create_handle_method:    创建句柄方式, 0 -> 通过ip直连, 1 -> 通过枚举
        :param resize_ratio:    resize
        :param rotation:    旋转
        :param get_one_frame_timeout_ms:    获取一帧的超时时间
        :param multicast_ip:    组播ip
        :param multicast_port:  组播port
        :param to_ping:     是否在初始化时ping相机
        """
        super().__init__()

        # 修正 ip
        if "ip" in kwargs:
            ip = kwargs.pop("ip")
            kwargs["_ip"] = ip

        # DeviceUserID
        self.DeviceUserID = None
        # 句柄打开标志
        self.is_handle_created_flag = False
        # 相机取流标志
        self.is_grabbing_flag = False
        # 相机打开标志
        self.is_opened_flag = False

        # 加载相机节点
        self.nodes = self.load_nodes(path=kwargs.pop("camera_nodes_path", None))

        # 加载相机参数
        self.native_params, self.custom_params = self.load_params(
            path=kwargs.pop("camera_params_path", None),
            ip=self.ip
        )
        # 获取CameraCustomParams类所有字段名称
        camera_custom_params_fields = [f.name for f in dataclasses.fields(CameraCustomParams)]
        # 从 kwargs 中更新相机参数
        for k, v in kwargs.items():
            if k in self.nodes.key.values:
                self.native_params[k] = v
            elif k in camera_custom_params_fields:
                self.custom_params[k] = v

        # 线程锁
        self.lock = Lock()

        # 计算机系统
        self.is_win = utils.is_win()

        # 相机用户自定义参数
        self.__dict__.update(dataclasses.asdict(CameraCustomParams(**self.custom_params)))

        # memcpy 函数
        self.memcpy_func = ctypes.cdll.msvcrt.memcpy if self.is_win else ctypes.CDLL("libc.so.6").memcpy

        # 被动取流回调函数
        self.CALL_BACK_FUN = None

        # 结构体
        # 设备信息
        self.stDevInfo: HIK.MV_CC_DEVICE_INFO = None
        # 帧
        self.stOutFrame: HIK.MV_FRAME_OUT = None
        # 帧信息
        self.stFrameInfo: HIK.MV_FRAME_OUT_INFO_EX = None

        # 相机帧数据指针
        self.data_buffer = None
        # 相机frame指针
        self.frame_buffer = None

        # payload size
        self.nPayloadSize = 0

        # 初始化SDK
        self.sdk_initialize()

        # ping 相机
        if self.to_ping:
            res = utils.ping_ip(self.ip, host_ip=self.host_ip)
            if not res:
                raise HikCameraError(f"ping [{self.ip}]{f" from [{self.host_ip}]" if self.host_ip is not None else ""} lost")
            else:
                _logger.debug(f"{self.identity} ping [{self.ip}] successfully")

    def __enter__(self) -> typing.Self:
        """
        Camera initialization : open, setup, and start grabbing frames from the device.
        :return:
        """
        # 创建句柄
        res = self.create_handle()
        if res != HIK.MV_OK:
            raise HikCameraError(f"create handle[{self.create_handle_method.name}] failed, error code[{self.mvs_error_code(res)}]")
        else:
            _logger.debug(f"{self.identity} create handle[{self.create_handle_method.name}] successfully")

        # 打开设备
        self.open_device()

        # 取流
        res = self.start_grabbing()
        if res != HIK.MV_OK:
            raise HikCameraError(f"start grabbing[{self.grab_method.name}] failed, error code[{self.mvs_error_code(res)}]")
        else:
            _logger.debug(f"{self.identity} start grabbing[{self.grab_method.name}] successfully")

        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        """
        Run camera termination code: stop grabbing frames and close the device.
        :param exc_type:
        :param exc_value:
        :param exc_tb:
        :return:
        """
        # 停止取流
        if self.is_grabbing_flag:
            res = self.stop_grabbing()
            if res != HIK.MV_OK:
                raise HikCameraError(f"stop grabbing failed, error code[{self.mvs_error_code(res)}]")
            else:
                _logger.debug(f"{self.identity} stop grabbing successfully")

        # 关闭设备
        if self.is_opened_flag:
            res = self.close_device()
            if res != HIK.MV_OK:
                _logger.warning(f"{self.identity} close device failed, error code[{self.mvs_error_code(res)}]")
            else:
                _logger.info(f"{self.identity} close device successfully")

        # 销毁句柄
        if self.is_handle_created_flag:
            res = self.destroy_handle()
            if res != HIK.MV_OK:
                _logger.warning(f"{self.identity} destroy handle failed, error code[{self.mvs_error_code(res)}]")
            else:
                _logger.debug(f"{self.identity} destroy handle successfully")

    def finalize(self):
        # 反初始化SDK, 释放资源, 只能使用一次
        self.sdk_finalize()

    # #################### 创建/销毁句柄 ####################
    def create_handle(self) -> int:
        """创建句柄"""
        '''
        MVS SDK 有 Bug: 
            在 linux 下 调用完"枚举设备" 接口后, 再调用"无枚举连接相机" 会无法打开相机.
            同一个进程的 SDK 枚举完成后不能再直连. 需要新建一个进程. 或者不枚举 直接直连就没问题
        '''
        if self.create_handle_method == CreateHandleMethod.Direct:
            # Instantiate a GigE device info structure
            stGigEDev = HIK.MV_GIGE_DEVICE_INFO()
            # Set the GigE device info structure's IP address to the camera's IP address
            stGigEDev.nCurrentIp = utils.ip_2_int(self.ip)
            # Set the GigE device info structure's network interface IP address to the network interface's IP address
            if self.host_ip is not None:
                stGigEDev.nNetExport = utils.ip_2_int(self.host_ip)
            # Instantiate a device info structure
            stDevInfo = HIK.MV_CC_DEVICE_INFO()
            stDevInfo.nTLayerType = HIK.MV_GIGE_DEVICE  # When using GigE cameras
            # Set the device info structure's GigE device info to the GigE device info structure
            stDevInfo.SpecialInfo.stGigEInfo = stGigEDev
        else:
            stDevInfo = self.get_devices_info_by_enum(self.ip)

        # Create a handle to reference the camera given its device info
        res = self.MV_CC_CreateHandle(stDevInfo)
        if res == HIK.MV_OK:
            self.is_handle_created_flag = True
            self.stDevInfo = stDevInfo
            self._ip = utils.int_2_ip(stDevInfo.SpecialInfo.stGigEInfo.nCurrentIp)
        return res

    def destroy_handle(self) -> int:
        """销毁句柄"""
        res = self.MV_CC_DestroyHandle()
        # 复位变量
        self.is_handle_created_flag = False
        return res

    # #################### 打开/关闭设备 ####################
    def open_device(self):
        """打开相机"""
        '''
        MV_CC_OpenDevice        ->  打开设备
            参数：   nAccessMode[IN]     ->      访问权限                  * 不可用
                        MV_ACCESS_Exclusive                     1       独占权限，其他APP只允许读CCP寄存器
                        MV_ACCESS_ExclusiveWithSwitch           2*      可以从5模式下抢占权限，然后以独占权限打开
                        MV_ACCESS_Control                       3       控制权限，其他APP允许读所有寄存器
                        MV_ACCESS_ControlWithSwitch             4*      可以从5的模式下抢占权限，然后以控制权限打开
                        MV_ACCESS_ControlSwitchEnable           5*      以可被抢占的控制权限打开
                        MV_ACCESS_ControlSwitchEnableWithKey    6*      可以从5的模式下抢占权限，然后以可被抢占的控制权限打开
                        MV_ACCESS_Monitor                       7       读模式打开设备，适用于控制权限下
                    nSwitchoverKey[IN] -> 切换访问权限时的密钥
            返回：   成功，返回MV_OK；失败，返回错误码
            备注：   根据设置的设备参数，找到对应的设备，连接设备。
                      调用接口时可不传入nAccessMode和nSwitchoverKey，此时默认设备访问模式为独占权限。
                      ！目前设备暂不支持   MV_ACCESS_ExclusiveWithSwitch            ->  2
                                        MV_ACCESS_ControlWithSwitch             ->  4
                                        MV_ACCESS_ControlSwitchEnable           ->  5
                                        MV_ACCESS_ControlSwitchEnableWithKey    ->  6   这四种抢占模式。
                      对于U3V设备，nAccessMode、nSwitchoverKey这两个参数无效。
        '''
        # 查看设备在 access mode 模式下是否可达
        res = self.is_device_accessible()
        if not res:
            raise HikCameraError(f"device[{self.ip}] unaccessible in access mode[{self.access_mode.name}]")
        else:
            _logger.debug(f"{self.identity} device accessible")

        # 打开相机
        res = self.MV_CC_OpenDevice(self.access_mode.value, 0)
        if res != HIK.MV_OK:
            raise HikCameraError(f"open device failed, error code[{self.mvs_error_code(res)}]")
        else:
            self.DeviceUserID = self["DeviceUserID"]
            _logger.info(f"{self.identity} open device successfully")

        # 非独占时 设置组播
        if not self.access_mode.is_exclusive():
            res = self.set_transmission_type()
            if res != HIK.MV_OK:
                raise HikCameraError(f"set transmission type[{self.access_mode.name}] failed, error code[{self.mvs_error_code(res)}]")
            else:
                _logger.info(f"{self.identity} set transmission type[{self.access_mode.name}] successfully")

        # 优化网络最佳包大小
        if self.stDevInfo.nTLayerType == HIK.MV_GIGE_DEVICE:
            self.optimize_packet_size()

        # 初始化 相机 native params
        self.init_native_params()

        # Mark the camera as open
        self.is_opened_flag = True

    def set_transmission_type(self) -> int:
        """设置组播"""
        # 获取 组播 ip 和 port
        multicast_ip = utils.ip_2_int(self.multicast_ip)
        multicast_port = self.multicast_port

        stTransmissionType = HIK.MV_TRANSMISSION_TYPE()
        ctypes.memset(ctypes.byref(stTransmissionType), 0, ctypes.sizeof(HIK.MV_TRANSMISSION_TYPE))
        stTransmissionType.enTransmissionType = HIK.MV_GIGE_TRANSTYPE_MULTICAST
        stTransmissionType.nDestIp = multicast_ip
        stTransmissionType.nDestPort = multicast_port

        return self.MV_GIGE_SetTransmissionType(stTransmissionType)

    def is_device_accessible(self) -> int:
        """判断相机是否可达"""
        return self.MV_CC_IsDeviceAccessible(self.stDevInfo, self.access_mode.value)

    def is_device_connected(self) -> int:
        """判断相机连接性"""
        return self.MV_CC_IsDeviceConnected()

    def close_device(self):
        """关闭相机"""
        res = self.MV_CC_CloseDevice()
        # 复位变量
        self.is_opened_flag = False
        return res

    # #################### 回调函数 ####################
    def init_image_callback(self):
        """初始化回调函数"""
        # 创建POINTER指针，指向图片数据
        pData = ctypes.POINTER(ctypes.c_ubyte)
        # 创建POINTER指针
        pFrameInfo = ctypes.POINTER(HIK.MV_FRAME_OUT_INFO_EX)

        # 创建一个c函数类型的对象
        if self.is_win:
            FrameInfoCallBack = ctypes.WINFUNCTYPE(None, pData, pFrameInfo, ctypes.c_void_p)
        else:
            FrameInfoCallBack = ctypes.CFUNCTYPE(None, pData, pFrameInfo, ctypes.c_void_p)

        # 生成回调函数
        self.CALL_BACK_FUN = FrameInfoCallBack(self.get_one_frame_callback)

    def register_image_callback_ex(self) -> int:
        """注册抓图回调"""
        '''
        MV_CC_RegisterImageCallBackEx()     ->  注册图像数据回调 
            MV_CAMCTRL_API  int __stdcall   MV_CC_RegisterImageCallBackEx( 
                void *  handle,  
                void(__stdcall *cbOutput)(  unsigned char *pData, 
                                            MV_FRAME_OUT_INFO_EX *pstFrameInfo, 
                                            void *pUser)  ,  
                void *  pUser)   
            参数
                handle      [IN]    设备句柄  
                cbOutput    [IN]    回调函数指针  
                pUser       [IN]    用户自定义变量
            返回  成功，返回MV_OK；失败，返回错误码 
            备注 
                通过该接口可以设置图像数据回调函数，在 MV_CC_CreateHandle() 之后即可调用。 
                图像数据采集有两种方式，两种方式不能复用：
                    1. 调用 MV_CC_RegisterImageCallBackEx() 设置图像数据回调函数，
                        然后调用 MV_CC_StartGrabbing() 开始采集，采集的图像数据在设置的回调函数中返回。 
                    2. 调用 MV_CC_StartGrabbing() 开始采集，
                        然后在应用层循环调用 MV_CC_GetOneFrameTimeout() 获取指定像素格式的帧数据，
                        获取帧数据时上层应用程序需要根据帧率控制好调用该接口的频率。 
        '''

        # def MV_CC_RegisterImageCallBackEx(self, CallBackFun, pUser)
        # 创建 用户自定义信息
        # self -> pUser = ctypes.cast(ctypes.pointer(ctypes.py_object(self)), ctypes.c_void_p)
        # int -> pUser = number
        # str -> strUser = ctypes.create_string_buffer(string.encode('utf-8')) pUser = cast(strUser, ctypes.c_void_p)
        return self.MV_CC_RegisterImageCallBackEx(self.CALL_BACK_FUN, None)

    # #################### 开始/停止取流 ####################
    def start_grabbing(self) -> int:
        """开始取流"""
        # method 3 -> 被动取流, MV_CC_RegisterImageCallBackEx
        if self.grab_method == GrabMethod.RegisterImageCallBackEx:
            # 初始化回调函数
            self.init_image_callback()
            # 注册回调函数
            res = self.register_image_callback_ex()
            if res != HIK.MV_OK:
                raise HikCameraError(f"register image callback failed, error code[{self.mvs_error_code(res)}]")
            else:
                _logger.debug(f"{self.identity} register image callback successfully")

        # Start grabbing frames from the camera
        res = self.MV_CC_StartGrabbing()
        if res == HIK.MV_OK:
            # method 1 -> 主动取流, MV_CC_GetOneFrameTimeout
            if self.grab_method == GrabMethod.GetOneFrameTimeout:
                # Get the payload size from the camera and store it in the payload size structure by reference
                self.nPayloadSize = self.getitem("PayloadSize")
                # Allocate a buffer to store the frame data.
                # You'll need memory for self.nPayloadSize unsigned 8-bit integers (0-255)
                self.data_buffer = (ctypes.c_ubyte * self.nPayloadSize)()
                # Instantiate a structure to hold the frame information
                self.stFrameInfo = HIK.MV_FRAME_OUT_INFO_EX()
                # Initialize the frame information structure to zero
                ctypes.memset(ctypes.byref(self.stFrameInfo), 0, ctypes.sizeof(self.stFrameInfo))
            # method 2 -> 主动取流, MV_CC_GetImageBuffer, MV_CC_FreeImageBuffer
            elif self.grab_method == GrabMethod.GetImageBuffer:
                self.stOutFrame = HIK.MV_FRAME_OUT()
                ctypes.memset(ctypes.byref(self.stOutFrame), 0, ctypes.sizeof(self.stOutFrame))
            else:
                pass

            self.is_grabbing_flag = True

        return res

    def stop_grabbing(self) -> int:
        """停止取流"""
        # 停止取流
        res = self.MV_CC_StopGrabbing()

        # 复位变量
        self.is_grabbing_flag = False

        del self.frame_buffer
        del self.data_buffer
        self.data_buffer = None
        self.frame_buffer = None

        return res

    # #################### 获取帧 ####################
    def get_one_frame(self) -> np.ndarray:
        """获取一帧画面, 需要循环调用, 可以重载"""
        with self.lock:
            if self.grab_method == GrabMethod.GetOneFrameTimeout:
                # method 1
                # Frame acquisition:
                # SDK C API will save the frame data to the buffer by reference (byref(self.data_buf))
                # and will save the frame information to the frame information structure by reference
                # (self.stFrameInfo, called by reference in the python wrapper for the C API)
                res = self.MV_CC_GetOneFrameTimeout(
                    pData=ctypes.byref(self.data_buffer),
                    nDataSize=self.nPayloadSize,
                    stFrameInfo=self.stFrameInfo,
                    nMsec=self.get_one_frame_timeout_ms,
                )
                if res != HIK.MV_OK:
                    raise HikCameraError(f"get one frame failed, error code[{self.mvs_error_code(res)}]")

                # 给 self.frame_buffer 开辟空间
                if self.frame_buffer is None:
                    self.frame_buffer = (ctypes.c_ubyte * self.stFrameInfo.nFrameLen)()

                # 将 self.data_buffer 复制到 self.image_buffer
                self.memcpy_func(ctypes.byref(self.frame_buffer), self.data_buffer, self.stFrameInfo.nFrameLen)

                # 转换为numpy数组
                image_data = self.convert_frame_buf_2_numpy_arr()
                # 调整图片 -> resize, rotation
                image_data = self.adjust_image(image_data)

                return image_data

            elif self.grab_method == GrabMethod.GetImageBuffer:
                # method 2
                res = self.MV_CC_GetImageBuffer(
                    stFrame=self.stOutFrame,
                    nMsec=self.get_one_frame_timeout_ms
                )
                if res != HIK.MV_OK:
                    raise HikCameraError(f"get one frame failed, error code[{self.mvs_error_code(res)}]")

                self.stFrameInfo = self.stOutFrame.stFrameInfo

                # 给 self.frame_buffer 开辟空间
                if self.frame_buffer is None:
                    self.frame_buffer = (ctypes.c_ubyte * self.stFrameInfo.nFrameLen)()

                # 将 self.data_buffer 复制到 self.image_buffer
                self.memcpy_func(ctypes.byref(self.frame_buffer), self.stOutFrame.pBufAddr, self.stFrameInfo.nFrameLen)

                # 转换为numpy数组
                image_data = self.convert_frame_buf_2_numpy_arr()
                # 调整图片 -> resize, rotation
                image_data = self.adjust_image(image_data)

                self.MV_CC_FreeImageBuffer(self.stOutFrame)

                return image_data

            else:
                raise HikCameraError(f"get_one_frame() shouldn't be called in grab method[{self.grab_method.name}]")

    def get_one_frame_callback(self, pData, pFrameInfo, pUser) -> np.ndarray:
        """
        回调函数，处理图像数据
        可重载
        :param pData:
        :param pFrameInfo:
        :param pUser:
        :return:
        """
        with self.lock:
            # 用户自定义信息
            # obj -> obj = ctypes.cast(pUser, ctypes.POINTER(ctypes.py_object)).contents.value
            # str -> string = str(cast(pUser, ctypes.c_char_p).value, encoding="utf-8")
            # int -> number = pUser
            # 帧信息 MV_FRAME_OUT_INFO_EX结构体 指针
            self.stFrameInfo = ctypes.cast(pFrameInfo, ctypes.POINTER(HIK.MV_FRAME_OUT_INFO_EX)).contents
            # 帧数据指针，保存每一帧的画面numpy数组，长度为st_frame_info.nFrameLen，类型为c_ubyte
            self.data_buffer = ctypes.cast(pData, ctypes.POINTER(ctypes.c_ubyte * self.stFrameInfo.nFrameLen)).contents

            # 给 self.frame_buffer 开辟空间
            if self.frame_buffer is None:
                self.frame_buffer = (ctypes.c_ubyte * self.stFrameInfo.nFrameLen)()

            # 将 self.data_buffer 复制到 self.image_buffer
            self.memcpy_func(ctypes.byref(self.frame_buffer), self.data_buffer, self.stFrameInfo.nFrameLen)

            # 转换为numpy数组
            image_data = self.convert_frame_buf_2_numpy_arr()
            # 调整图片 -> resize, rotation
            image_data = self.adjust_image(image_data)

            return image_data

    def convert_frame_buf_2_numpy_arr(self) -> np.ndarray:
        """将 frame_buf 转变为 numpy数组"""
        # 帧信息
        # nWidth = stFrameInfo.nWidth
        # nHeight = stFrameInfo.nHeight
        # enPixelType = stFrameInfo.enPixelType
        # nFrameNum = stFrameInfo.nFrameNum
        # nDevTimeStampHigh = stFrameInfo.nDevTimeStampHigh
        # nDevTimeStampLow = stFrameInfo.nDevTimeStampLow
        # nHostTimeStamp = stFrameInfo.nHostTimeStamp
        # nFrameLen = stFrameInfo.nFrameLen
        '''
        frombuffer -> 将data以流的形式读入转化成nparray对象
              numpy.frombuffer(buffer, dtype=float, count=-1, offset=0)
              参数:
                buffer: 缓冲区，它表示暴露缓冲区接口的对象。
                dtype： 代表返回的数据类型数组的数据类型。默认值为0。
                count： 代表返回的ndarray的长度。默认值为-1。
                offset：偏移量，代表读取的起始位置。默认值为0。
        '''
        # numpy 数组长度为 [nWidth * nHeight], 数据类型为np.uint8
        image_data: np.ndarray = np.frombuffer(buffer=self.frame_buffer, count=self.stFrameInfo.nFrameLen, dtype=np.uint8, offset=0)

        # 灰度图
        if self.stFrameInfo.enPixelType == HIK.PixelType_Gvsp_Mono8:
            image_data = np.reshape(image_data, (self.stFrameInfo.nHeight, self.stFrameInfo.nWidth))
        # RGB
        elif self.stFrameInfo.enPixelType == HIK.PixelType_Gvsp_RGB8_Packed:
            image_data = np.reshape(image_data, (self.stFrameInfo.nHeight, self.stFrameInfo.nWidth, -1))
        else:
            raise NotImplementedError(f"frame enPixelType[{self.stFrameInfo.enPixelType}] is not supported to convert to numpy array now")

        return image_data

    def adjust_image(self, image_data: np.ndarray) -> np.ndarray:
        """
        调整图片
        :param image_data:
        :return:
        """
        # resize
        if self.resize_ratio != 1.0 or self.resize_ratio is not None:
            image_data = cv2.resize(
                image_data, None, None,
                fx=self.resize_ratio,
                fy=self.resize_ratio,
                interpolation=cv2.INTER_AREA
            )

        # rotation
        if self.rotation.is_rotate():
            image_data = cv2.rotate(image_data, self.rotation.value)

        return image_data

    # #################### 获取/设置参数 ####################
    def getitem(self, key: str) -> typing.Any:
        """
        Get a camera setting value given its key
        :param key:
        :return:
        """
        if key in ["rotation", "resize_ratio", "image_size"]:
            return self.get_custom_param(key=key)
        else:
            # Get key setting data type
            dtype = self.nodes[self.nodes.key == key]["dtype"].iloc[0]
            # todo 验证 windows 和 linux 的统一性
            # Retrieve parameter getter from MVS SDK for the given data type
            if dtype == "iboolean":
                get_func = self.MV_CC_GetBoolValue
                stValue = ctypes.c_bool()
                attr = "value"
            elif dtype == "ienumeration":
                get_func = self.MV_CC_GetEnumValue
                stValue = HIK.MVCC_ENUMVALUE()
                ctypes.memset(ctypes.byref(stValue), 0, ctypes.sizeof(HIK.MVCC_ENUMVALUE))
                attr = "nCurValue"
            elif dtype == "ifloat":
                get_func = self.MV_CC_GetFloatValue
                stValue = HIK.MVCC_FLOATVALUE()
                ctypes.memset(ctypes.byref(stValue), 0, ctypes.sizeof(HIK.MVCC_FLOATVALUE))
                attr = "fCurValue"
            elif dtype == "iinteger":
                get_func = self.MV_CC_GetIntValue
                stValue = HIK.MVCC_INTVALUE()
                ctypes.memset(ctypes.byref(stValue), 0, ctypes.sizeof(HIK.MVCC_INTVALUE))
                attr = "nCurValue"
            elif dtype == "istring":
                get_func = self.MV_CC_GetStringValue
                stValue = HIK.MVCC_STRINGVALUE()
                ctypes.memset(ctypes.byref(stValue), 0, ctypes.sizeof(HIK.MVCC_STRINGVALUE))
                attr = "chCurValue"
            # TODO set register function is not defined
            # elif dtype == "register":
            #     get_func = self.MV_CC_RegisterEventCallBackEx
            else:
                # 获取函数名称
                raise TypeError(f"illegal dtype[{dtype}] in getitem({key})")

            # get parameter from the camera
            with self.lock:
                res = get_func(key, stValue)
                if res != HIK.MV_OK:
                    raise HikCameraError(f"{get_func.__name__}({key}) failed, error code[{self.mvs_error_code(res)}]")

            # decode parameter
            value = getattr(stValue, attr)
            if dtype == "istring":
                value = value.decode()
                show = value
            elif dtype == "ienumeration":
                enum_range = self.nodes[self.nodes.key == key]["enum_range"].iloc[0]
                show = enum_range.get(value, f"{value}[unknown enum name]")
            else:
                show = value

            _logger.debug(f"{self.identity} {get_func.__name__}({key}) = {show}")

            return value

    def setitem(self, key: str, value: typing.Any):
        """
        Set a camera setting to a given value.
        :param key:
        :param value:
        :return:
        """
        if key in ["rotation", "resize_ratio"]:
            self.set_custom_param(key=key, value=value)
        else:
            if not self.access_mode.has_control_permission():
                _logger.warning(f"{self.identity} setitem({key}) shouldn't be called in access mode[{self.access_mode.name}]")
                return

            # Get key setting data type
            dtype = self.nodes[self.nodes.key == key]["dtype"].iloc[0]
            # Retrieve parameter setter from MVS SDK for the given data type
            if dtype == "iboolean":
                set_func = self.MV_CC_SetBoolValue
                params = (key, value)
            elif dtype == "ienumeration":
                if isinstance(value, str):
                    set_func = self.MV_CC_SetEnumValueByString
                else:
                    set_func = self.MV_CC_SetEnumValue
                params = (key, value)
            elif dtype == "ifloat":
                set_func = self.MV_CC_SetFloatValue
                params = (key, value)
            elif dtype == "iinteger":
                set_func = self.MV_CC_SetIntValue
                params = (key, value)
            elif dtype == "istring":
                set_func = self.MV_CC_SetStringValue
                params = (key, value)
            elif dtype == "icommand":
                set_func = self.MV_CC_SetCommandValue
                params = (key,)
            # TODO set register function is not defined
            # elif dtype == "register":
            #     set_func = self.MV_CC_RegisterEventCallBackEx
            else:
                raise TypeError(f"illegal dtype[{dtype}] in setitem({key})")

            # set parameter of the camera
            with self.lock:
                res = set_func(*params)
                if res != HIK.MV_OK:
                    raise HikCameraError(f"{set_func.__name__}{params} failed, error code[{self.mvs_error_code(res)}]")

            # 更新 userid
            if key == "DeviceUserID":
                self.DeviceUserID = value

            _logger.debug(f"{self.identity} {set_func.__name__}{params} done")

    __getitem__ = getitem
    __setitem__ = setitem

    def get_custom_param(self, key):
        if key == "rotation":
            return self.get_rotation()
        elif key == "resize_ratio":
            return self.get_resize_ratio()
        elif key == "image_size":
            return self.get_image_size()
        else:
            raise HikCameraError(f"illegal parameter in get_custom_param({key})")

    def set_custom_param(self, key, value):
        if key == "rotation":
            self.set_rotation(int(value))
        elif key == "resize_ratio":
            self.set_resize_ratio(None if value is None else float(value))
        else:
            raise HikCameraError(f"illegal parameter in set_custom_param({key}, {value})")

    def get_rotation(self) -> int:
        _logger.debug(f"{self.identity} get_rotation() = {self.rotation.name}")
        return self.rotation.value

    def set_rotation(self, rotation: int):
        with self.lock:
            try:
                self.rotation = Rotation(rotation)
                _logger.debug(f"{self.identity} set_rotation({rotation}) done")
            except Exception as err:
                raise ValueError(f"set_rotation({rotation}) error") from err

    def get_resize_ratio(self) -> typing.Optional[float]:
        _logger.debug(f"{self.identity} get_resize_ratio() = {self.resize_ratio}")
        return self.resize_ratio

    def set_resize_ratio(self, resize_ratio: typing.Optional[float]):
        with self.lock:
            self.resize_ratio = resize_ratio
            _logger.debug(f"{self.identity} set_resize_ratio({self.resize_ratio}) done")

    def get_image_size(self) -> tuple[int, int]:
        """
        获得 图片尺寸
        :return:  height, width
        """
        width = self["Width"]
        height = self["Height"]

        # resize
        if self.resize_ratio != 1.0 or self.resize_ratio is not None:
            width = int(width * self.resize_ratio)
            height = int(height * self.resize_ratio)

        # rotate 90°
        if self.rotation.is_90():
            height, width = width, height

        _logger.debug(f"{self.identity} get_image_size() = ({height}, {width})")
        return height, width

    def init_native_params(self):
        """
        用于相机参数设置，在相机打开后使用
        可以重载，
        :return:
        """
        for key, value in self.native_params.items():
            self.setitem(key, value)

    def optimize_packet_size(self):
        """
        获取最佳的packet size, 并设置网络包大小
        :return:
        """
        # 获取最佳的packet size，该接口目前只支持GigE设备
        nPacketSize = self.MV_CC_GetOptimalPacketSize()
        if nPacketSize <= 0:
            raise HikCameraError(f"{self.MV_CC_GetOptimalPacketSize.__name__}() failed, error nPacketSize = {nPacketSize}")
        else:
            _logger.debug(f"{self.identity} {self.MV_CC_GetOptimalPacketSize.__name__}() = {nPacketSize}")
        # 设置网络包大小
        # GevSCPSPacketSize -> 网络包大小。＞0,与相机相关。一般范围在220-9156，步进为8
        self["GevSCPSPacketSize"] = nPacketSize

    # #################### 保存图片 ####################
    def save_image(self, path: str, nQuality: int = 99, iMethodValue: int = 3) -> int:
        """
        保存图片，使用 MV_CC_SaveImageToFileEx，支持 .jpg, .png, .bmp
        :param path:
        :param nQuality:        JPG编码质量(50-99]，默认99
        :param iMethodValue:    Bayer格式转为RGB24的插值方法 0-快速 1-均衡 2-最优 3-最优+，默认3
        :return:
        """
        # 文件格式
        _, file_format = os.path.splitext(path)
        file_format = file_format[1:].lower()
        if file_format == "jpg" or file_format == "jpeg":
            image_type = HIK.MV_Image_Jpeg
        elif file_format == "png":
            image_type = HIK.MV_Image_Png
        elif file_format == "bmp":
            image_type = HIK.MV_Image_Bmp
        else:
            raise TypeError(f"saved format[{file_format}] is not supported")

        # 文件夹
        saved_dir = os.path.dirname(path)
        os.makedirs(saved_dir, exist_ok=True)

        # 保存图片文件参数结构体
        stSaveParam = HIK.MV_SAVE_IMAGE_TO_FILE_PARAM_EX()
        # 图片尺寸
        stSaveParam.nWidth = self.stFrameInfo.nWidth
        stSaveParam.nHeight = self.stFrameInfo.nHeight
        # 输入数据的像素格式
        stSaveParam.enPixelType = self.stFrameInfo.enPixelType
        # 输入数据缓存
        stSaveParam.pData = ctypes.cast(self.frame_buffer, ctypes.POINTER(ctypes.c_ubyte))
        # 输入数据大小
        stSaveParam.nDataLen = self.stFrameInfo.nFrameLen
        # 输入图片格式
        stSaveParam.enImageType = image_type
        # 输入文件路径
        stSaveParam.pcImagePath = ctypes.create_string_buffer(path.encode())
        # JPG编码质量(50-99]
        stSaveParam.nQuality = min(max(51, nQuality), 99)
        # Bayer格式转为RGB24的插值方法 0-快速 1-均衡 2-最优 3-最优+
        stSaveParam.iMethodValue = min(max(0, iMethodValue), 3)

        # 保存
        res = self.MV_CC_SaveImageToFileEx(stSaveParam)
        if res != HIK.MV_OK:
            raise HikCameraError(f"save image to file[{path}] failed, error code[{self.mvs_error_code(res)}]")
        return res

    # --------------------------------------------------------------------------- #
    # 属性方法
    # --------------------------------------------------------------------------- #
    @property
    def ip(self) -> str:
        """
        相机ip
        :return:
        """
        if not hasattr(self, "_ip"):
            # 获取相机IP地址
            self._ip = utils.int_2_ip(self["GevCurrentIPAddress"])
        return self._ip

    @property
    def identity(self):
        """相机可读身份"""
        if self.DeviceUserID is not None:
            return f"camera[{self.ip}|{self.DeviceUserID}]"
        else:
            return f"camera[{self.ip}]"

    # --------------------------------------------------------------------------- #
    # 静态方法
    # --------------------------------------------------------------------------- #
    @staticmethod
    def enum_all_ips() -> list[str]:
        """
        枚举相机
        :return: 所有链接的相机ip地址
        """
        # 创建新的进程, 绕过 hik sdk 枚举后无法 "无枚举连接相机"(使用 ip 直连)的 bug
        # 获取 enum_all_ips.py 路径
        basename = os.path.basename(__file__)
        py_file = __file__.replace(basename, "enum_all_ips.py")
        # 确保文件存在
        if not os.path.isfile(py_file):
            raise FileNotFoundError(f"python script file[{py_file}] not found")

        # cmd 执行并， 获取输出
        # todo 修改 -m hikrobot_camera.enum_all_ips
        res = subprocess.run(
            [sys.executable, "-m", "hikrobot_camera.enum_all_ips"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            # timeout=2,
        )
        if res.stderr:
            raise HikCameraError(f"enum all ips failed, error: {res.stderr}")

        # 提取输
        ips = res.stdout.strip().split(";")
        # 去除 None 值
        ips = list(filter(None, ips))

        _logger.debug(f"[camera] all enumerated camera ips = {ips}")
        return ips

    @staticmethod
    def save_image_by_cv(path: str, image: np.ndarray, **kwargs) -> int:
        """
        保存图片，使用opencv，支持 .jpg, .png, .bmp
        :param path:
        :param image:       图像 numpy数组
        :param kwargs:      jpg_quality，JPEG图片质量[0,100]，默认100
                            png_compression，PNG压缩等级[0,9]，默认0（0:无压缩，9:最大压缩, 数值越大，文件越小但压缩越慢）
        :return:
        """
        # 文件格式
        _, file_format = os.path.splitext(path)
        file_format = file_format[1:].lower()
        if file_format not in ["jpg", "jpeg", "png", "bmp"]:
            raise TypeError(f"saved format[{file_format}] is not supported")

        # 文件夹
        saved_dir = os.path.dirname(path)
        os.makedirs(saved_dir, exist_ok=True)

        if file_format in ["jpg", "jpeg"]:
            # 图片质量
            quality = kwargs.get("jpg_quality", 100)
            quality = min(max(0, quality), 100)
            params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        elif file_format == "png":
            compression = kwargs.get("png_compression", 0)
            compression = min(max(0, compression), 9)
            params = [cv2.IMWRITE_PNG_COMPRESSION, compression]
        else:
            params = list()

        # 保存
        success = cv2.imwrite(path, image, params)
        if success:
            return HIK.MV_OK
        else:
            raise HikCameraError(f"save image[{path}] by cv failed")

    @staticmethod
    def mvs_error_code(res) -> str:
        """
        mvs sdk 错误代码 可视化
        :param res: 错误代码
        :return:
        """
        return f"{res: x} | {HikErrorMap.map(res)}"

    # --------------------------------------------------------------------------- #
    # 类方法
    # --------------------------------------------------------------------------- #
    @classmethod
    def sdk_initialize(cls):
        """初始化SDK"""
        # 4.2.0.3 版本以上才有这个方法
        if float(cls.sdk_version[:3]) <= 4.2:
            return

        if not getattr(cls, "_initialize", False):
            res = cls.MV_CC_Initialize()
            if res != HIK.MV_OK:
                raise HikCameraError(f"initialize mvs sdk failed, error code[{cls.mvs_error_code(res)}]")
            else:
                setattr(cls, "_initialize", True)
                _logger.debug(f"[camera] initialize mvs sdk successfully")

    @classmethod
    def sdk_finalize(cls):
        """反初始化SDK, 释放资源"""
        # 4.2.0.3 版本以上才有这个方法
        if float(cls.sdk_version[:3]) <= 4.2:
            return

        if getattr(cls, "_initialize", False):
            res = cls.MV_CC_Finalize()
            if res != HIK.MV_OK:
                raise HikCameraError(f"finalize mvs sdk failed, error code[{cls.mvs_error_code(res)}]")
            else:
                setattr(cls, "_initialize", False)
                _logger.debug(f"[camera] finalize mvs sdk successfully")

    @classmethod
    def get_devices_info_by_enum(cls, ip: typing.Optional[str] = None) -> typing.Union[dict[str, HIK.MV_CC_DEVICE_INFO], HIK.MV_CC_DEVICE_INFO]:
        """
        通过枚举获取设备信息， 保存在类属性 cls.devices_info
        ip is None -> 所有链接相机的 MV_CC_DEVICE_INF
        ip is not None -> 该ip对应相机的 MV_CC_DEVICE_INF
        :param ip: IP address of the camera. Defaults to None.
        :return: IP is None -> 所有链接相机的 MV_CC_DEVICE_INF，字典
                 IP is not None -> 该ip对应相机的 MV_CC_DEVICE_INF

        """
        devices_info = dict()
        # Instantiate a device info list structure
        stDevList = HIK.MV_CC_DEVICE_INFO_LIST()
        # Set device communication protocol
        # only GIGE DEVICE
        nTLayerType = HIK.MV_GIGE_DEVICE  # | MV_USB_DEVICE
        # Enumerate all devices on the network by MVS SDK APIs call.
        res = cls.MV_CC_EnumDevices(nTLayerType, stDevList)
        if res != HIK.MV_OK:
            raise HikCameraError(f"enum cameras failed, error code[{cls.mvs_error_code(res)}]")
        if stDevList.nDeviceNum == 0:
            raise ConnectionError(f"enum cameras failed, no camera is connected")

        # Iterate through all devices on the network and retrieve devices IPs
        for i in range(0, stDevList.nDeviceNum):
            # Cast MVS device info structure pointer to ctypes device info structure pointer and retrieve device info
            stDevInfo = ctypes.cast(stDevList.pDeviceInfo[i], ctypes.POINTER(HIK.MV_CC_DEVICE_INFO)).contents
            # if stDeviceInfo.nTLayerType == HIK.MV_GIGE_DEVICE:
            # Get the device IP address from the device info structure
            _ip = utils.int_2_ip(stDevInfo.SpecialInfo.stGigEInfo.nCurrentIp)
            devices_info[_ip] = stDevInfo

        cls.devices_info = {_ip: devices_info[_ip] for _ip in sorted(devices_info)}

        if ip is None:
            return cls.devices_info
        else:
            if ip in cls.devices_info:
                return cls.devices_info[ip]
            else:
                raise ConnectionError(f"camera[{ip}] is not connected")

    @classmethod
    def create_all_cameras(cls, ips: typing.Optional[list] = None, **kwargs) -> dict[str, typing.Self]:
        """
        Class method that returns a dictionary of all connected cameras.
        :param ips: List of IP addresses of the cameras to connect to. Defaults to None.
        :return: Dictionary of all connected Hik cameras. Class MultiHikCameras
        """
        if ips is None:
            ips = cls.enum_all_ips()
        ips = sorted(ips)
        cameras = MultiHikrobotCameras({ip: cls(_ip=ip, **kwargs) for ip in ips})

        return cameras

    @classmethod
    def create_camera(cls, **kwargs) -> typing.Self:
        """
        Class method that returns a cameras object
        :param kwargs:  ip -> 相机ip地址
                        index -> 相机序号
        :return:
        """
        # 相机ip
        ip: typing.Union[str, None] = kwargs.pop("ip", None)
        # 相机序号
        index: typing.Union[int, None] = kwargs.pop("index", None)

        # 默认枚举相机，并定位第一个相机
        if ip is None and index is None:
            ips = cls.enum_all_ips()
            if not ips:
                raise ConnectionError(f"no camera is connected")
            camera = cls(_ip=ips[0], **kwargs)
        # 定位 ip地址
        elif ip is not None:
            camera = cls(_ip=ip, **kwargs)
        # 定位 index
        else:
            ips = cls.enum_all_ips()
            if index >= len(ips) or index < 0:
                raise ValueError(f"camera index[{index}] out of range[0-{len(ips) - 1}]")
            camera = cls(_ip=ips[index], **kwargs)
        return camera

    @classmethod
    def get_sdk_version(cls):
        """
        获取 SDK 版本号
        :return:
        """
        if not hasattr(cls, "sdk_version"):
            sdk_version_int = int(cls.MV_CC_GetSDKVersion())
            cls.sdk_version = "%x.%x.%x.%x" % (sdk_version_int >> 24 & 0xFF, sdk_version_int >> 16 & 0xFF, sdk_version_int >> 8 & 0xFF, sdk_version_int & 0xFF)

            _logger.debug(f"[camera] mvs sdk version = {cls.sdk_version}")
        return cls.sdk_version

    @classmethod
    def load_params(cls, path: typing.Optional[str] = None, ip: typing.Optional[str] = None) -> typing.Union[dict, tuple[dict, dict]]:
        """
        从 camera_params.yml 文件中加载相机参数
        :param path:
        :param ip:
        :return:    若未指定 ip，则返回所有相机参数字典。
                    若指定 ip，则返回 (native_params, custom_params) 二元组
        """
        def decode_params(params: typing.Optional[dict]) -> tuple[dict, dict]:
            """解码 native/custom 两类参数，保证返回 dict"""
            if not isinstance(params, dict):
                return dict(), dict()
            return params.get("native") or dict(), params.get("custom") or dict()

        def merge_params(local_: dict, global_: dict) -> dict:
            """局部参数优先，合并全局参数"""
            return {**global_, **local_}

        # 缓存机制：只加载一次
        if not hasattr(cls, "_params_cache"):
            if not path:
                basename = os.path.basename(__file__)
                path = __file__.replace(basename, "camera_params.yml")
            # 确保文件存在
            if not os.path.isfile(path):
                raise FileNotFoundError(f"camera params yaml file not found: {path}")

            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or dict()

            # 解析全局参数
            global_native, global_custom = decode_params(raw.pop("global", None))
            cls._global_params = {"native": global_native, "custom": global_custom}

            # 解析局部参数（按 IP）
            params_cache = dict()
            for ip_key, param_block in sorted(raw.items()):
                local_native, local_custom = decode_params(param_block)
                params_cache[ip_key] = {
                    "native": merge_params(local_native, global_native),
                    "custom": merge_params(local_custom, global_custom),
                }

            cls._params_cache = params_cache

        # 若未指定 IP，则返回全部
        if ip is None:
            return cls._params_cache

        # 返回指定 IP 的参数（若无则使用全局）
        ip_params = cls._params_cache.get(ip, cls._global_params)
        return decode_params(ip_params)

    @classmethod
    def load_nodes(cls, path: str = None) -> pd.DataFrame:
        """
        Read the MvCameraNode-CH.csv file and return a pandas DataFrame
        which contains the camera settings key names, dependencies, and data types.
        :param path:
        :return:
        """
        def get_key_before_square(key):
            if "[" in key:
                key = key[: key.index("[")]
            return key.strip()

        def get_depend_in_square(key):
            key = key.strip()
            if "[" in key:
                return key[key.index("[") + 1: -1]
            return ""

        def parse_range(value: str, dtype: str):
            """
            将 range 字段根据 dtype 进行解析：
              - IEnumeration → 转为 {int: str} 字典
              - 其他类型 → None
            """
            if dtype.strip().lower() == "ienumeration":
                lines = value.splitlines()
                enum_dict = dict()
                for line in lines:
                    line = line.strip()
                    if not line or "：" not in line:
                        continue
                    k, v = line.split("：", 1)
                    try:
                        enum_dict[int(k.strip())] = v.strip()
                    except ValueError:
                        continue
                return enum_dict
            else:
                return None

        if not hasattr(cls, "nodes"):
            if not path:
                basename = os.path.basename(__file__)
                path = __file__.replace(basename, "MvCameraNode-CH.csv")

            # 确保文件存在
            if not os.path.isfile(path):
                raise FileNotFoundError(f"camera nodes csv file not found: {path}")

            csv = pd.read_csv(path)
            # 取第二、三列
            col_name, col_type, col_range = csv.columns[1], csv.columns[2], csv.columns[3]
            cls.nodes = pd.DataFrame(
                {
                    "key": csv[col_name].map(get_key_before_square),            # 相机参数名称
                    "depend": csv[col_name].map(get_depend_in_square),          # 相机参数关联，如果有
                    "dtype": csv[col_type].map(lambda x: x.strip().lower()),    # 相机参数类型
                    "enum_range": [parse_range(r, t) for r, t in zip(csv[col_range], csv[col_type])],    # 相机数值范围定义
                }
            )
        return cls.nodes


class HikCameraError(Exception):
    pass
