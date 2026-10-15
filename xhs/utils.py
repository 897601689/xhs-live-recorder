"""与业务无关的小工具：时间格式化、文件名清洗、大小与时长格式化"""

import os
from datetime import datetime

# 项目根目录（xhs/ 的上一级）：ffmpeg 的兜底查找位置，打包后由 config.PROGRAM_DIR 接管
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def current_time_formatted() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def sanitize_filename(name: str) -> str:
    """过滤 Windows 非法文件名字符,并去除首尾空格与句点"""
    illegal = r'\/:*?"<>|'
    cleaned = "".join(ch for ch in name if ch not in illegal)
    return cleaned.strip().strip('.')


def transform_int_to_time(seconds: int) -> str:
    return datetime.fromtimestamp(seconds / 1000).strftime('%Y-%m-%d %H-%M-%S')


def format_file_size(size_bytes):
    """
    格式化文件大小

    Args:
        size_bytes (int): 文件大小（字节）

    Returns:
        str: 格式化后的文件大小字符串
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
