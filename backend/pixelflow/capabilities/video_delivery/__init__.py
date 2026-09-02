"""视频交付能力：同步拼接成片，不恢复旧 M06 merge job 编排。"""

from .providers.content_app import ContentAppVideoMergeAdapter

__all__ = ["ContentAppVideoMergeAdapter"]
