"""验证视频 Operation 恢复编排只从通用 operations 包暴露。"""

from pixelflow.operations.resume import VideoOperationResumer, VideoQuotaResumer


def test_operation_resumers_have_single_public_implementation() -> None:
    """旧 VideoAgent 兼容入口已删除，M06 恢复只保留一套实现。"""

    assert VideoOperationResumer.__module__ == "pixelflow.operations.resume"
    assert VideoQuotaResumer.__module__ == "pixelflow.operations.resume"
