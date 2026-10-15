"""把代码画的应用图标落成一枚真 .ico：打包后的 exe 文件图标、固定到任务栏的图块都只能从它取

运行时那枚是 QPixmap 现画的（xhs/ui/app_icon.py），清晰、且仓库里不用躺二进制资源；
但没有 .ico，双击 dist 里那个 exe 看到的就是白纸一张，快捷方式和任务栏图块也一样。
笔画全按边长比例给，所以直接画 256 也是同一个形状，大图不会糊成马赛克。

用法：python tools/make_icon.py     （产物 app_icon.ico 提交进仓库，打包时不用再跑）
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication, QImage, QImageReader

from xhs.ui import app_icon

# 16~48 是界面真正用到的档位，64/128/256 给资源管理器大图与快捷方式
# （.ico 里 256 记成 0，Windows 靠这个约定认出一张是 256 而不是 0 宽）
ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
OUT = os.path.join(ROOT, "app_icon.ico")


def _png_bytes(image: QImage) -> bytes:
    """QImage -> PNG 字节：Qt 的 .ico 写出只存单帧，多帧容器只能自己拼"""
    path = os.path.join(os.path.dirname(OUT), f".tmp_{image.width()}.png")
    try:
        assert image.save(path, "PNG"), path
        with open(path, "rb") as fp:
            return fp.read()
    finally:
        if os.path.exists(path):
            os.unlink(path)


def build() -> None:
    frames = [(size, _png_bytes(app_icon.pixmap(size).toImage())) for size in ICON_SIZES]
    entries = bytearray()
    offset = 6 + 16 * len(frames)          # ICONDIR 头 + 每帧一条 ICONDIRENTRY
    for size, data in frames:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    blob = struct.pack("<HHH", 0, 1, len(frames)) + bytes(entries) + b"".join(d for _, d in frames)
    with open(OUT, "wb") as fp:
        fp.write(blob)
    print(f"{os.path.basename(OUT)}: {len(frames)} 帧 / {len(blob)} 字节")


def verify() -> None:
    """读回来逐帧对一遍：容器解析得动、八档尺寸齐、每帧和现画的逐像素相同

    不拿 QIcon 对：它的 pixmap(16) 会挑 32 那帧返回（Qt 的取图策略本来就允许给大图），
    量的是选图不是文件，这里要验的是文件本身。
    """
    with open(OUT, "rb") as fp:
        head = fp.read(6)
    assert struct.unpack("<HHH", head) == (0, 1, len(ICON_SIZES)), head
    reader = QImageReader(OUT)
    assert reader.imageCount() == len(ICON_SIZES), reader.imageCount()
    for index, size in enumerate(ICON_SIZES):
        assert reader.jumpToImage(index), index
        assert reader.size() == QSize(size, size), (index, reader.size())
        stored = reader.read().convertToFormat(QImage.Format.Format_ARGB32)
        drawn = app_icon.pixmap(size).toImage().convertToFormat(QImage.Format.Format_ARGB32)
        diff = sum(1 for y in range(drawn.height()) for x in range(drawn.width())
                   if stored.pixel(x, y) != drawn.pixel(x, y))
        assert diff == 0, f"{size}px 帧与现画的差 {diff} 像素"
    print(f"  {len(ICON_SIZES)} 帧全部与现画逐像素一致（{ICON_SIZES[0]}…{ICON_SIZES[-1]}px）")


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)        # QPixmap 要有 Q(Gui)Application 才画得出来
    build()
    verify()
    print("OK")
