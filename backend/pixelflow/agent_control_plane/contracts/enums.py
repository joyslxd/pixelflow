"""Agent Runtime 对 Python 与 TypeScript 共同冻结的线协议枚举。"""

from enum import StrEnum


class OrchestrationMode(StrEnum):
    """对话创建后不可由普通 context PATCH 改写的编排归属。"""

    FRONTEND_V2 = "frontend_v2"
    VIDEO_AGENT_V2 = "video_agent_v2"


class WorkflowKind(StrEnum):
    """可以持久化为独立 Workflow 的业务类型。"""

    IMAGE = "image"
    VIDEO = "video"
    PPT = "ppt"
    VIDEO_ANALYSIS = "video_analysis"


class WorkflowStatus(StrEnum):
    """Workflow 业务投影的稳定状态。"""

    DRAFT = "draft"
    AWAITING_USER = "awaiting_user"
    RUNNING = "running"
    PAUSED_QUOTA = "paused_quota"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TurnStatus(StrEnum):
    """用户输入从接收到终态的队列状态。"""

    ACCEPTED = "accepted"
    QUEUED = "queued"
    PROCESSING = "processing"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"


class ExternalJobStatus(StrEnum):
    """Agent 所拥有的外部任务引用状态。"""

    CREATED = "created"
    POLLING = "polling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    EXPIRED = "expired"


class AgentEventType(StrEnum):
    """首批前端可感知事件类型。"""

    RUN_STATE_CHANGED = "run.state_changed"
    CONTEXT_COMPRESSION_STARTED = "context.compression_started"
    CONTEXT_COMPRESSION_PROGRESSED = "context.compression_progressed"
    CONTEXT_COMPRESSION_COMPLETED = "context.compression_completed"
    CONTEXT_COMPRESSION_FAILED = "context.compression_failed"
    INPUT_STATE_CHANGED = "input.state_changed"
    MESSAGE_UPSERTED = "message.upserted"
    WORKFLOW_PROGRESSED = "workflow.progressed"
    INTERRUPT_OPENED = "interrupt.opened"
    INTERRUPT_RESPONDED = "interrupt.responded"
    INTERRUPT_CLOSED = "interrupt.closed"
    EXTERNAL_JOB_STATE_CHANGED = "external_job.state_changed"
    EXTERNAL_JOB_QUOTA_STATE_CHANGED = "external_job.quota_state_changed"
    AGENT_PLAN_CREATED = "agent.plan.created"
    AGENT_PLAN_UPDATED = "agent.plan.updated"
    AGENT_STEP_STARTED = "agent.step.started"
    AGENT_STEP_PROGRESSED = "agent.step.progressed"
    AGENT_STEP_COMPLETED = "agent.step.completed"
    AGENT_STEP_FAILED = "agent.step.failed"
    AGENT_THINKING_STARTED = "agent.thinking.started"
    AGENT_THINKING_DELTA = "agent.thinking.delta"
    AGENT_THINKING_COMPLETED = "agent.thinking.completed"
    AGENT_REASONING_SUMMARY_DELTA = "agent.reasoning_summary.delta"
    AGENT_REASONING_SUMMARY_COMPLETED = "agent.reasoning_summary.completed"
    AGENT_TOOL_STARTED = "agent.tool.started"
    AGENT_TOOL_PROGRESS = "agent.tool.progress"
    AGENT_TOOL_COMPLETED = "agent.tool.completed"
    AGENT_TOOL_FAILED = "agent.tool.failed"
    AGENT_OPERATION_UPDATED = "agent.operation.updated"
    AGENT_ARTIFACT_UPDATED = "agent.artifact.updated"
    AGENT_RESPONSE_DELTA = "agent.response.delta"
    AGENT_RESPONSE_COMPLETED = "agent.response.completed"
    AGENT_CONFIRMATION_REQUESTED = "agent.confirmation.requested"
    ERROR_RAISED = "error.raised"
