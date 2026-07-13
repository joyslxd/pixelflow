"""PixelFlow P0 结构化用户偏好入口。

当前只保存结构化偏好、负向规则、默认参数和最近反馈；语义记忆留给 P1。
"""

from pixelflow.preferences.extract import extract_structured_preferences
from pixelflow.preferences.store import MemoryUserPreferenceStore, SQLUserPreferenceStore, UserPreferenceRecord, UserPreferenceStore

__all__ = ["MemoryUserPreferenceStore", "SQLUserPreferenceStore", "UserPreferenceRecord", "UserPreferenceStore", "extract_structured_preferences"]
