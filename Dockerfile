FROM ubuntu:24.04 AS base
LABEL authors="yy"

# 设置非交互模式，避免 tzdata 等交互式安装卡住
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai

# 必要工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    net-tools curl iputils-ping unzip wget traceroute ca-certificates dos2unix

# opencv 相关
RUN apt-get install -y --no-install-recommends \
    libjpeg8-dev libpng-dev libtiff-dev libwebp-dev libopenjp2-7-dev \
    libtbb-dev libopenblas0 \
    libgl1 libglib2.0-0 libsm6 libxrender1 libxext6

# 虚拟屏幕 vnc
RUN apt-get install -y --no-install-recommends \
    xvfb x11vnc

# 安装 uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
# 设置 PATH
ENV PATH="/root/.local/bin:$PATH"

# 安装 HIKRobot MVS
# https://www.hikrobotics.com/cn2/source/support/software/MVS_Linux_STD_V4.6.0_250808.zip
RUN cd /tmp && \
    wget --referer="https://www.hikrobotics.com/cn/machinevision/service/download/?module=0" \
    https://www.hikrobotics.com/cn2/source/support/software/MVS_Linux_STD_V4.6.0_250808.zip && \
    unzip MVS_Linux_STD_V4.6.0_250808.zip && \
    dpkg -i MVS-4.6.0_x86_64_20250808.deb && \
    cd /
# 设置 PATH
ENV MVCAM_COMMON_RUNENV=/opt/MVS/lib

RUN apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/*


FROM base AS runtime

# 拷贝文件
COPY . /hikrobot-camera
WORKDIR /hikrobot-camera

RUN uv sync


RUN dos2unix entrypoint.sh && \
    chmod +x entrypoint.sh

ENTRYPOINT ["/hikrobot-camera/entrypoint.sh"]


# sudo docker build -t Yybrook/hikrobot-camera:0.1 .
# sudo docker run --name cam_01 --net=host -it --rm Yybrook/hikrobot-camera:0.1