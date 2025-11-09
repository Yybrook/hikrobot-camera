## An API to control HIKRobot cameras which is supported in Linux/Windows


* 参考自：https://github.com/DIYer22/hik_camera.git
* 仅在 GIGE 相机上进行了测试



* Docker运行
```bash
sudo docker build -t Yybrook/hikrobot-camera:0.1 .
sudo docker run --name cam_01 --net=host -it --rm Yybrook/hikrobot-camera:0.1
```