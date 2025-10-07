#!/usr/bin/env python3

if __name__ == "__main__":

    from .hikrobot_camera import HikrobotCamera

    print(";".join(sorted(HikrobotCamera.get_devices_info_by_enum())))
