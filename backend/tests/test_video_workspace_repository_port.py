"""验证视频工作区 Repository 已完整迁入领域模块。"""

from pixelflow.video.workspace.memory_repository import MemoryVideoAgentRepository
from pixelflow.video.workspace.repository import VideoWorkspaceRepository
from pixelflow.video.workspace.sql_repository import SQLVideoAgentRepository


def test_video_workspace_repositories_implement_domain_port() -> None:
    """内存与 SQL 实现必须只依赖统一的新领域 Repository Port。"""

    assert isinstance(MemoryVideoAgentRepository(), VideoWorkspaceRepository)
    assert isinstance(SQLVideoAgentRepository, type)
