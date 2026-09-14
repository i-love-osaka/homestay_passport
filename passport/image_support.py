import os
import tempfile
from contextlib import contextmanager

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

# 图片处理适配层：统一处理 Pillow 的方向修正，以及 OpenCV 不直接支持的 HEIC/HEIF。
# 视图和导出模块都从这里读取图片，避免每个调用方重复实现格式判断和临时文件清理。
# 在任何调用方打开图片之前，先注册 HEIC/HEIF 支持。
register_heif_opener()


def open_image(path):
    # 返回已经应用 EXIF 方向信息的 Pillow 图片对象；调用方负责通过 with 或 close 释放资源。
    # exif_transpose 把手机拍摄方向真正应用到像素，后续 OCR 无需重复处理方向。
    image = Image.open(path)
    return ImageOps.exif_transpose(image)


@contextmanager
def processing_path(path):
    # 这个上下文管理器保证：调用方拿到的始终是可交给 OpenCV 的路径，退出时临时 JPEG 一定被清理。
    # 返回 OpenCV 可以读取的路径；遇到 HEIC/HEIF 时先转换为临时 JPEG。
    extension = os.path.splitext(path)[1].lower()
    if extension not in {".heic", ".heif"}:
        # OpenCV 可直接读取常规格式时复用原路径，避免不必要的临时文件。
        yield path
        return

    converted_path = None
    try:
        with open_image(path) as image:
            converted = image.convert("RGB")
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp:
                converted.save(temp, format="JPEG", quality=95)
                converted_path = temp.name
        yield converted_path
    finally:
        # 转换文件只在当前识别流程中使用，退出上下文后立即删除。
        if converted_path:
            try:
                os.unlink(converted_path)
            except FileNotFoundError:
                pass
