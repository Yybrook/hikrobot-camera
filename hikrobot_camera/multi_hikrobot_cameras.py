from threading import Thread, Lock


class MultiHikrobotCameras(dict):
    def __getattr__(self, attr):
        # 如果 attr 不可调用，则以字典形式返回属性
        if not callable(getattr(next(iter(self.values())), attr)):
            return {ip: getattr(camera, attr) for ip, camera in self.items()}

        # 如果属性是可调用的，则并发执行
        def func(*args, **kwargs):
            threads = dict()
            res = dict()
            lock = Lock()

            def _func(_ip, _cam):
                try:
                    result = getattr(_cam, attr)(*args, **kwargs)
                except Exception as err:
                    # 保存异常对象
                    result = err
                with lock:
                    res[_ip] = result

            for ip, camera in self.items():
                t = Thread(target=_func, args=(ip, camera), daemon=True)
                t.start()
                threads[ip] = t

            # 等待所有线程结束
            for t in threads.values():
                t.join()

            # 排序（确保顺序一致）
            return {ip: res[ip] for ip in sorted(res)}

        return func

    def __enter__(self):
        self.__getattr__("__enter__")()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.__getattr__("__exit__")(exc_type, exc_value, traceback)
