import logging
import cv2
from hikrobot_camera import HikrobotCamera
from hikrobot_camera.cv_show import CvShow


def init_logger():
    # 创建logger对象
    logger = logging.getLogger()
    # 设置全局最低等级（让所有handler能接收到）
    logger.setLevel(logging.DEBUG)
    # 控制台 Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    # 添加 handler 到 logger
    logger.addHandler(console_handler)

    # logging.getLogger('asyncio').setLevel(logging.INFO)


if __name__ == '__main__':

    try:
        init_logger()

        # 获取SDK版本
        _sdk_version = HikrobotCamera.get_sdk_version()
        print("sdk version: ", _sdk_version)

        # 获取所有相机节点
        # _nodes = HikrobotCamera.load_nodes()
        # print(_nodes)

        # 获取相机参数
        # _params = HikrobotCamera.load_params()
        # print(_params)

        # # 通过枚举获取所有设备信息
        # _devices_info = HikrobotCamera.get_devices_info_by_enum()
        # print(_devices_info)

        # # 枚举获得所有相机ip
        # _cam_ips = HikrobotCamera.enum_all_ips()
        # print(_cam_ips)

        # # 连接一个相机
        # # 虚拟相机只能使用枚举连接相机
        # with HikrobotCamera.create_camera(
        #         ip='192.168.31.230',
        #         grab_method=2, access_mode=1, create_handle_method=1,
        #         ExposureAuto="Continuous", ExposureTime=None,
        #         GainAuto="Continuous", Gain=None,
        #         Brightness=100,
        #         TriggerMode="Off",
        # ) as _cam1:
        #     try:
        #         _frame = _cam1.get_one_frame()
        #         print(f"Camera[{_cam1.ip}] shape: {_frame.shape}")
        #     except Exception as err:
        #         print(f"Camera[{_cam1.ip}] raised error: {err}")

        # 连接所有相机
        with HikrobotCamera.create_all_cameras(
                grab_method=2, access_mode=1, create_handle_method=1,
                ExposureAuto="Continuous", ExposureTime=None,
                GainAuto="Continuous", Gain=None,
                Brightness=100,
                TriggerMode="Off",
                AcquisitionFrameRateEnable=True, AcquisitionFrameRate=1,
        ) as _cams, CvShow() as _show:
            for _idx, _key in enumerate(_show):
                _frames = _cams.get_one_frame()
                for _ip, _frame in _frames.items():
                    if isinstance(_frame, BaseException):
                        print(f"Camera[{_ip}] raised error: {_frame}")
                        continue
                    else:
                        # print(f"Camera[{_ip}] shape: {_frame.shape}")
                        _image = cv2.resize(_frame, None, None, fx=0.2, fy=0.2, interpolation=cv2.INTER_AREA)
                        _show.imshow(_image, window=_ip)
                if _key == "q":
                    break

    except KeyboardInterrupt:
        print("KeyboardInterrupt")
    finally:
        print("Ended")