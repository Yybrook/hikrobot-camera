import cv2


class CvShow:
    """
    A context manager for displaying images and capturing keyboard input using OpenCV.
    Supports both RGB and grayscale images.
    """
    _destroyed = False

    def __enter__(self):
        """
        Enter the context manager, resetting the destroyed flag.
        :return:
        """
        CvShow._destroyed = False
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        """
        Exit the context manager, destroy all OpenCV windows.
        :param exc_type:
        :param exc_value:
        :param exc_tb:
        :return:
        """
        CvShow._destroyed = True
        cv2.destroyAllWindows()

    @staticmethod
    def imshow(image, window="default"):
        """
        Display an image in a named window, converting RGB to BGR if needed.
        :param image:
        :param window:
        :return:
        """
        if image.ndim == 3 and image.shape[-1] == 3:
            # RGB to BGR
            # image = image[..., ::-1]
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        cv2.imshow(window, image)

    @staticmethod
    def get_key(delay=1):
        """
        Capture a keyboard input.
        :param delay: int, default 1, The delay in milliseconds for `cv2.waitKey`.
        :return: str or int, The pressed key as a character (if ASCII) or the key code.
        """
        key_idx = cv2.waitKey(delay)
        return chr(key_idx) if 0 < key_idx < 256 else key_idx

    def __next__(self):
        """
        Retrieve the next key press.
        :return:
        """
        return self.get_key()

    def __iter__(self):
        """
        Make the instance iterable, returning self.
        :return:
        """
        return self

