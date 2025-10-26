from hikrobot_camera import  HikrobotCamera


def main():
    print("hello from hikrobot camera")
    # 获取SDK版本
    _sdk_version = HikrobotCamera.get_sdk_version()
    print(f"mvs sdk version {_sdk_version}")


if __name__ == "__main__":
    main()
