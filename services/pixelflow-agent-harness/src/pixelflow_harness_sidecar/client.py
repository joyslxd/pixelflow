"""供 PixelFlow Gateway 使用的 Sidecar 内部 HTTP Client。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from .contracts import HarnessRunEvent, HarnessRunRequest, HarnessRunState


class AgentHarnessSidecarClientError(RuntimeError):
    """表示 Sidecar 网络、鉴权或稳定协议错误，不携带下游响应正文。"""


class AgentHarnessSidecarConflictError(AgentHarnessSidecarClientError):
    """表示 Run 幂等身份与冻结请求摘要发生冲突。"""


class AgentHarnessSidecarClient:
    """类似 Java Feign Client：只传输稳定 DTO，不泄漏 Harness 私有 Session 类型。"""

    def __init__(
        self,
        *,
        base_url: str,
        service_jwt: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """创建 Client；生产地址必须是 HTTPS，本机 loopback 测试允许 HTTP。"""

        normalized = base_url.rstrip("/")
        if not normalized:
            raise ValueError("Sidecar base_url 不能为空")
        if not service_jwt:
            raise ValueError("Sidecar 服务凭据不能为空")
        if not normalized.startswith("https://") and not normalized.startswith("http://127.0.0.1:"):
            raise ValueError("Sidecar 生产地址必须使用 HTTPS")
        self._base_url = normalized
        self._service_jwt = service_jwt
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def create_run(self, request: HarnessRunRequest) -> HarnessRunState:
        """以稳定 Idempotency-Key 创建或回读同一 Sidecar Run。"""

        response = await self._request(
            "POST",
            "/internal/v1/runs",
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": request.run_request_key},
        )
        return self._parse_state(response)

    async def get_run(self, run_id: str) -> HarnessRunState | None:
        """读取 Run 状态；404 只表示该稳定 Run 尚不存在。"""

        response = await self._request("GET", f"/internal/v1/runs/{run_id}", allow_not_found=True)
        return None if response is None else self._parse_state(response)

    async def activate_run(self, run_id: str) -> HarnessRunState:
        """通知 Sidecar：Gateway 已落库 binding，可安全开始本次 Run。"""

        response = await self._request("POST", f"/internal/v1/runs/{run_id}/activate")
        assert response is not None
        return self._parse_state(response)

    async def stream_events(
        self,
        run_id: str,
        *,
        after_sequence: int,
    ) -> AsyncIterator[HarnessRunEvent]:
        """消费公开 SSE，并拒绝畸形事件或非单调序列。"""

        if after_sequence < 0:
            raise ValueError("after_sequence 不能小于零")
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        previous = after_sequence
        try:
            async with self._client.stream(
                "GET",
                f"{self._base_url}/internal/v1/runs/{run_id}/events",
                params={"after_sequence": after_sequence},
                headers=headers,
            ) as response:
                if response.status_code != httpx.codes.OK:
                    self._raise_for_status(response.status_code)
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = HarnessRunEvent.model_validate_json(line[6:])
                    except ValueError as error:
                        raise AgentHarnessSidecarClientError("Sidecar SSE 事件协议无效") from error
                    if event.run_id != run_id or event.sequence <= previous:
                        raise AgentHarnessSidecarClientError("Sidecar SSE 序列无效")
                    previous = event.sequence
                    yield event
        except httpx.HTTPError as error:
            raise AgentHarnessSidecarClientError("Sidecar SSE 网络请求失败") from error

    async def aclose(self) -> None:
        """关闭本 Client 自己创建的 HTTP 连接池；注入 Client 由调用方管理。"""

        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response | None:
        """发送单次内部请求，并把下游错误收敛为固定异常类型。"""

        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=request_headers,
            )
        except httpx.HTTPError as error:
            raise AgentHarnessSidecarClientError("Sidecar HTTP 请求失败") from error
        if response.status_code == httpx.codes.NOT_FOUND and allow_not_found:
            return None
        if response.status_code >= httpx.codes.BAD_REQUEST:
            self._raise_for_status(response.status_code)
        return response

    def _headers(self) -> dict[str, str]:
        """构造服务身份 Header，不记录或返回凭据。"""

        return {"Authorization": f"Bearer {self._service_jwt}"}

    @staticmethod
    def _parse_state(response: httpx.Response) -> HarnessRunState:
        """严格解析 Sidecar 状态 DTO。"""

        try:
            return HarnessRunState.model_validate(response.json())
        except ValueError as error:
            raise AgentHarnessSidecarClientError("Sidecar 状态协议无效") from error

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        """只基于 HTTP 状态映射错误，禁止回显下游响应正文。"""

        if status_code == httpx.codes.CONFLICT:
            raise AgentHarnessSidecarConflictError("Sidecar Run 身份冲突")
        if status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise AgentHarnessSidecarClientError("Sidecar 服务身份校验失败")
        raise AgentHarnessSidecarClientError("Sidecar 请求被拒绝或不可用")
