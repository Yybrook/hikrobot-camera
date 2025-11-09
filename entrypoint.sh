#!/bin/bash

# 启动虚拟屏幕
# Xvfb：X virtual framebuffer，虚拟的 X 服务器，它不需要真实的显示器，也就是“虚拟屏幕”。
# :99：指定虚拟显示器号为 99（X 服务器可以有多个显示器 :0、:1 等）。
# -screen 0 1024x768x16：创建第 0 个屏幕，分辨率 1024x768，颜色深度 16 位。
# &：后台运行，不阻塞当前 shell。
Xvfb :99 -screen 0 1024x768x16 &

# DISPLAY 是 Linux 系统用来指定 X 服务器的环境变量。
# :99 指向上面启动的虚拟屏幕 Xvfb 的显示编号。
# 所有 GUI 程序（如 OpenCV、Qt）都会读取 DISPLAY 来知道该在哪个屏幕显示窗口。
export DISPLAY=:99

# 启动 VNC
# x11vnc：把一个已经存在的 X 服务器（这里是 Xvfb）暴露为 VNC 服务，允许远程访问虚拟屏幕。
# -display :99：连接到 Xvfb 的虚拟屏幕 99。
# -nopw：不设置 VNC 密码，直接允许连接（本地网络测试用）。
# -forever：客户端断开后，VNC 服务保持运行，不会退出。
# -rfbport: 设置端口号
x11vnc -display :99 -nopw -forever -rfbport 5910 &

# 运行
uv run -m test.hik_camera_test
