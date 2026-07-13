"""Gateway 层共享工具函数。"""


def sanitize_log_param(value: str) -> str:
    """去掉控制字符，避免日志注入。"""
    return value.replace("\n", "").replace("\r", "").replace("\x00", "")
