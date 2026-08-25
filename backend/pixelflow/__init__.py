"""PixelFlow 新架构根包。

旧 LangGraph 工作流已由 Harness Sidecar 替代；根包不再导出图编排对象，避免任意
领域导入时隐式加载旧运行时依赖。
"""
