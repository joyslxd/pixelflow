"""质检阶段能力入口。

QC 阶段检查已经产出的结果，而不是重新审查策划方案；阻塞失败会触发生成重试，
非阻塞风险只记录为 warning。
"""

from pixelflow.qc.check import qc_check
from pixelflow.qc.models import QCItem, QCResult

__all__ = ["QCItem", "QCResult", "qc_check"]
