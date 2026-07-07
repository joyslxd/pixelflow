#!/usr/bin/env python3
"""
Borgrise AI 内容创作平台执行脚本。

命令示例：
    python run_generation.py image-to-video --image-url URL --prompt "..." [--duration 5] [--ratio "9:16"]
    python run_generation.py text-to-video --prompt "..." [--duration 10] [--ratio "9:16"]
    python run_generation.py reference-mode-video --image-urls '["URL"]' --prompt "..." [--duration 10] [--ratio "9:16"]
    python run_generation.py native-audio-reference-video --image-urls '["URL"]' --prompt "..." [--duration 10] [--ratio "9:16"]
    python run_generation.py long-reference-mode-video --image-urls '["URL"]' --prompts '["segment1", "segment2"]' [--total-duration 30]
    python run_generation.py long-native-audio-reference-video --image-urls '["URL"]' --prompts '["segment1", "segment2"]' [--total-duration 30]
    python run_generation.py resume-long-reference-mode-video --progress-file /abs/progress.json [--prompts-file /abs/prompts.json]
    python run_generation.py text-to-image --prompt "..." [--ratio "1:1"] [--size "1080p"] [--num-images 4]
    python run_generation.py reference-image --reference-images '["URL"]' --prompt "..." [--ratio "1:1"] [--size "4K"] [--max-images 1]
    python run_generation.py image-edit --image-url URL --prompt "..."
    python run_generation.py batch-text-to-image --prompts '["prompt1", "prompt2", ...]' [--ratio "1:1"]
    python run_generation.py create-virtual-human-asset --image-url URL --asset-name NAME
    python run_generation.py poll --task-id TASK_ID

环境变量：
    BORGRISE_BASE_URL: API base URL，默认 https://test-video.borgrise.com/api

鉴权说明：
    生成/查询接口使用 content-app 登录用户的 Authorization 透传。网关收到前端请求后，
    会把原始 Authorization 写入 ContextVar；本脚本从 ContextVar 读取，不再支持
    BORGRISE_API_TOKEN、账号密码自动登录等静态扣费身份。
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

# 基础配置
BASE_URL = os.environ.get("BORGRISE_BASE_URL", "https://test-video.borgrise.com/api")
SKIP_SSL_VERIFY = os.environ.get("BORGRISE_SKIP_SSL_VERIFY", "").lower() in {"1", "true", "yes", "on"}
SSL_CONTEXT = ssl._create_unverified_context() if SKIP_SSL_VERIFY else None

# 默认模型
DEFAULT_IMAGE_MODEL = "seeddream-5.0"
DEFAULT_VIDEO_MODEL = "seedance-2.0"
SUPPORTED_RATIOS = {"1:1", "9:16", "16:9"}
SUPPORTED_IMAGE_QUALITIES = {"all", "480p", "720p", "1080p", "2K", "3K", "4K", "5K", "6K", "7K", "8K"}
DEFAULT_IMAGE_QUALITY_BY_MODEL = {
    "gpt-image-2": "4K",
    "seeddream-4.5": "2K",
    "seeddream-5.0": "2K",
    "nanobanana-pro": "1080p",
    "nano-banana": "1080p",
}
SEEDANCE_MAX_SEGMENT_DURATION = 10
SAFE_MAX_LONG_VIDEO_DURATION = 30

# 轮询配置
POLL_INTERVAL = 5  # 秒
VIDEO_POLL_TIMEOUT = int(os.environ.get("BORGRISE_VIDEO_POLL_TIMEOUT", "3600"))  # 视频生成默认最多等 1 小时。
VIDEO_MERGE_REQUEST_TIMEOUT = int(os.environ.get("BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT", "3600"))  # 视频合并是同步接口，默认最多等 1 小时。
IMAGE_POLL_TIMEOUT = int(os.environ.get("BORGRISE_IMAGE_POLL_TIMEOUT", "600"))  # 图片生成默认最多等 10 分钟。
VIDEO_ANALYSIS_POLL_TIMEOUT = int(os.environ.get("BORGRISE_VIDEO_ANALYSIS_POLL_TIMEOUT", "900"))  # 视频分析/拆解默认最多等 15 分钟。
PPT_POLL_TIMEOUT = int(os.environ.get("BORGRISE_PPT_POLL_TIMEOUT", "7200"))  # 智能 PPT 默认最多等 2 小时。
_cli_poll_timeout: int | None = None  # 由 --poll-timeout 命令行参数临时覆盖当前命令的业务默认值。

# 重试配置
MAX_REQUEST_RETRIES = int(os.environ.get("BORGRISE_MAX_RETRIES", "3"))
STATUS_POLL_ERROR_RECOVERY_ATTEMPTS = int(os.environ.get("BORGRISE_STATUS_POLL_ERROR_RECOVERY_ATTEMPTS", "3"))
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
QUOTA_INSUFFICIENT_STATUS_CODE = 402
QUOTA_INSUFFICIENT_KEYWORDS = (
    "额度不足",
    "余额不足",
    "没有有效的额度",
    "有效的额度",
    "扣费失败",
    "剩余额度",
    "充值",
    "quota insufficient",
    "insufficient quota",
    "insufficient balance",
    "payment required",
    "not enough quota",
)


def _effective_poll_timeout(default_timeout: int) -> int:
    """返回最终轮询超时时间：命令行参数优先，否则使用调用方传入的业务默认值。"""
    return _cli_poll_timeout if _cli_poll_timeout is not None else default_timeout


def validate_ratio(ratio: str) -> dict | None:
    """拒绝当前 Borgrise GPT/视频工作流不支持的画面比例。"""
    if ratio not in SUPPORTED_RATIOS:
        return {
            "error": True,
            "message": f"Unsupported ratio '{ratio}'. Use one of: 1:1, 9:16, 16:9.",
            "supported_ratios": sorted(SUPPORTED_RATIOS),
        }
    return None


def normalize_image_quality(size: str) -> str:
    """把旧版图片尺寸值归一到 Borgrise 计费配置使用的质量档位。"""
    normalized = str(size).strip()
    legacy_map = {
        "1024x1024": "1080p",
        "1024*1024": "1080p",
        "1536x1024": "2K",
        "1024x1536": "2K",
    }
    if normalized in legacy_map:
        mapped = legacy_map[normalized]
        print(f"  ℹ️  Mapping legacy image size '{normalized}' to Borgrise quality '{mapped}'.")
        return mapped
    return normalized


def validate_image_quality(size: str) -> dict | None:
    normalized = normalize_image_quality(size)
    if normalized not in SUPPORTED_IMAGE_QUALITIES:
        return {
            "error": True,
            "message": (
                f"Unsupported image quality '{size}'. "
                f"Use one of: {', '.join(sorted(SUPPORTED_IMAGE_QUALITIES))}."
            ),
            "supported_image_qualities": sorted(SUPPORTED_IMAGE_QUALITIES),
        }
    return None


def default_image_quality_for_model(model: str, fallback: str = "1080p") -> str:
    return DEFAULT_IMAGE_QUALITY_BY_MODEL.get(model, fallback)


def validate_image_model_quality(model: str, size: str) -> dict | None:
    """校验图片质量格式，模型级支持关系以 content-app 实时配置和接口为准。

    前端图片编辑确认卡会调用 `/api/modelParamConfig/listByCategory/image_generate`
    获取当前模型可选比例和清晰度。这里不再维护模型级白名单，避免 content-app
    配置更新后 Python 侧仍用旧规则提前拦截用户已经确认的参数。
    """
    _ = model
    return validate_image_quality(size)


def validate_video_duration(duration: int, model: str) -> dict | None:
    """校验单次视频生成时长不能超过已知模型限制。"""
    if duration <= 0:
        return {"error": True, "message": "Duration must be a positive integer"}
    if model == "seedance-2.0" and duration < 5:
        return {
            "error": True,
            "message": "seedance-2.0 supports video durations from 5s to 10s per single call.",
            "requested_duration": duration,
            "min_single_call_duration": 5,
        }
    if model == "seedance-2.0" and duration > SEEDANCE_MAX_SEGMENT_DURATION:
        return {
            "error": True,
            "message": (
                f"seedance-2.0 supports up to {SEEDANCE_MAX_SEGMENT_DURATION}s per single call. "
                "Use long-reference-mode-video with exact 10s segment prompts for longer videos."
            ),
            "requested_duration": duration,
            "max_single_call_duration": SEEDANCE_MAX_SEGMENT_DURATION,
        }
    return None


def validate_positive_count(count: int, field_name: str) -> dict | None:
    """校验输出数量，避免用户请求的 N 张图片被静默压成 1 张。"""
    if count <= 0:
        return {"error": True, "message": f"{field_name} must be a positive integer"}
    return None


def extract_result_urls(data: Any) -> list[str]:
    """尽力从单图、多图或视频任务结果中提取 URL。"""
    urls: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith("http://") or value.startswith("https://"):
                urls.append(value)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key in ("url", "urls", "imageUrl", "imageUrls", "image_url", "videoUrl", "videoUrls", "video_url", "result", "results", "images", "videos"):
                if key in value:
                    visit(value[key])

    visit(data)
    seen = set()
    deduped = []
    for url in urls:
        if url not in seen:
            deduped.append(url)
            seen.add(url)
    return deduped


def get_headers(model: str = "", bill_type: int = 0,
                 duration: int = 1, size: str = "720p",
                 model_header: str = "ModelType") -> dict[str, str]:
    """生成带当前用户 Authorization 和 Borgrise 必需自定义头的请求头。

    Borgrise API 需要的关键自定义头：
      - ModelType/modelType：模型名。content-app_ec 图片端点用 ModelType，
        视频端点用 modelType。
      - billType：2 = 图片按张计费，3 = 视频按秒计费。
      - apiModelParamObj：模型参数 JSON 字符串，例如 {"size":"720p"}。
      - duration：生成时长，视频按秒，图片为 1。

    鉴权头不再来自配置文件里的固定 token，而是来自当前请求的 content-app
    Authorization。这样后续 content-app 才能按真实登录用户扣费、查资产和写历史。
    """
    headers = {
        "Authorization": _current_authorization(),
        "Content-Type": "application/json"
    }
    if model:
        headers[model_header] = model
    if bill_type:
        headers["billType"] = str(bill_type)
    if duration:
        headers["duration"] = str(duration)
    # apiModelParamObj 保存模型参数配置。
    api_param = {"size": size}
    headers["apiModelParamObj"] = json.dumps(api_param)
    return headers


def _looks_token_expired(payload: dict[str, Any]) -> bool:
    """从 HTTP/JSON 响应形态中判断 Borgrise token 是否过期。"""
    haystack = " ".join(
        str(payload.get(key, ""))
        for key in ("code", "error", "message", "msg", "detail", "status")
    ).upper()
    if "TOKEN_EXPIRED" in haystack:
        return True

    data = payload.get("data")
    if isinstance(data, dict):
        return _looks_token_expired(data)
    return False


def _current_authorization() -> str:
    """读取当前请求的 content-app Authorization。

    这里故意使用延迟 import，保证这个脚本作为命令行文件被导入时不会因为应用层
    模块初始化顺序报错。真正发起计费接口前如果没有请求上下文，会抛出清晰错误。
    """
    from app.gateway.content_app_auth_context import require_current_authorization

    return require_current_authorization()


def is_quota_insufficient(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        if value.get("quota_insufficient") is True:
            return True
        if value.get("status_code") == QUOTA_INSUFFICIENT_STATUS_CODE:
            return True
        haystack = " ".join(
            str(value.get(key, ""))
            for key in ("message", "msg", "error", "detail", "code", "status")
        ).lower()
        if any(keyword.lower() in haystack for keyword in QUOTA_INSUFFICIENT_KEYWORDS):
            return True
        return any(is_quota_insufficient(child) for child in value.values())
    if isinstance(value, list):
        return any(is_quota_insufficient(item) for item in value)
    text = str(value).lower()
    return any(keyword.lower() in text for keyword in QUOTA_INSUFFICIENT_KEYWORDS)


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_error_message(value: Any, fallback: str = "") -> str:
    if isinstance(value, dict):
        for key in ("message", "msg", "error", "detail"):
            message = value.get(key)
            if message:
                return str(message)
        data = value.get("data")
        if data is not None:
            nested = _extract_error_message(data, "")
            if nested:
                return nested
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback.strip() or "额度不足，请充值后重试"


def _quota_error_response(payload: Any, *, status_code: int | None = None, fallback_message: str = "") -> dict:
    message = _extract_error_message(payload, fallback_message)
    return {
        "error": True,
        "quota_insufficient": True,
        "non_retryable": True,
        "status_code": status_code or QUOTA_INSUFFICIENT_STATUS_CODE,
        "message": message,
        "details": payload,
    }


def _normalize_quota_error(result: dict) -> dict:
    if is_quota_insufficient(result):
        return _quota_error_response(result, status_code=result.get("status_code"), fallback_message=str(result.get("message") or ""))
    return result


def _apply_auth_header(headers: dict[str, str]) -> dict[str, str]:
    """把当前用户 Authorization 写入请求头，覆盖调用方误传的固定 token。"""
    updated = dict(headers)
    updated["Authorization"] = _current_authorization()
    return updated


def _send_request(url: str, body: bytes | None, headers: dict[str, str], method: str, *, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
            result = json.loads(response.read().decode("utf-8"))
            if is_quota_insufficient(result):
                return _quota_error_response(result, status_code=getattr(response, "status", None))
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        parsed = _safe_json_loads(error_body)
        if e.code == QUOTA_INSUFFICIENT_STATUS_CODE or is_quota_insufficient(parsed) or is_quota_insufficient(error_body):
            return _quota_error_response(parsed or error_body, status_code=e.code, fallback_message=error_body)
        return {
            "error": True,
            "status_code": e.code,
            "message": error_body
        }


def make_request(endpoint: str, data: dict | None = None, method: str = "POST",
                  custom_headers: dict[str, str] | None = None,
                  _retry_on_token_expired: bool = True) -> dict:
    """``_make_request_impl`` 的薄包装：只加内部调试用的 trace 记录。

    trace 记录只在当前请求带了 conversation_id 上下文时才生效（见
    ``pixelflow.tracing.conversation_trace``），旧流程/CLI 调用不受影响。
    """
    from pixelflow.tracing import record_trace_event_background

    started_at = time.monotonic()
    result = _make_request_impl(
        endpoint, data, method=method, custom_headers=custom_headers,
        _retry_on_token_expired=_retry_on_token_expired,
    )
    record_trace_event_background(
        "vendor_call",
        {
            "endpoint": endpoint,
            "method": method,
            "request": _trace_truncate(data),
            "response": _trace_truncate(result),
            "duration_ms": round((time.monotonic() - started_at) * 1000),
            "error": bool(isinstance(result, dict) and result.get("error")),
        },
    )
    return result


def _trace_truncate(value: Any, *, max_chars: int = 4000) -> Any:
    """裁剪超长字符串，避免 base64 图片/大段响应把 trace 表撑爆。"""
    if isinstance(value, str):
        return value if len(value) <= max_chars else f"{value[:max_chars]}...(截断)"
    if isinstance(value, dict):
        return {k: _trace_truncate(v, max_chars=max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_trace_truncate(v, max_chars=max_chars) for v in value[:50]]
    return value


def _make_request_impl(endpoint: str, data: dict | None = None, method: str = "POST",
                        custom_headers: dict[str, str] | None = None,
                        _retry_on_token_expired: bool = True) -> dict:
                  _retry_on_token_expired: bool = True,
                  request_timeout: int = 30) -> dict:
    """向 Borgrise API 发起 HTTP 请求，并对临时错误重试。

    会重试：
      - HTTP 429（限流）和 5xx（服务端错误）。
      - 网络层错误，如 URLError、TimeoutError、OSError。

    重试间隔使用指数退避：2s、4s、8s……。可通过 BORGRISE_MAX_RETRIES 环境变量
    覆盖默认重试次数。
    """
    url = f"{BASE_URL}{endpoint}"
    # 优先使用调用方传入的自定义头；否则使用轮询等简单请求的默认 JSON 头。
    try:
        if custom_headers:
            headers = _apply_auth_header(custom_headers)
        else:
            headers = _apply_auth_header({
                "Content-Type": "application/json"
            })
    except Exception as exc:
        return {"error": True, "message": str(exc)}

    if data:
        body = json.dumps(data).encode("utf-8")
    else:
        body = None

    last_error: dict | None = None
    for attempt in range(MAX_REQUEST_RETRIES):
        try:
            result = _send_request(url, body, headers, method, timeout=request_timeout)
            if _retry_on_token_expired and _looks_token_expired(result):
                return {"error": True, "status_code": 401, "message": "content-app Authorization 已过期，请重新登录后重试", "details": result}
            result = _normalize_quota_error(result)
            if result.get("quota_insufficient"):
                return result
            if not result.get("error"):
                return result

            status_code = result.get("status_code")
            if result.get("non_retryable"):
                return result
            retryable = status_code in RETRYABLE_HTTP_CODES
            if retryable and attempt < MAX_REQUEST_RETRIES - 1:
                wait = (2 ** attempt) * 2
                print(f"  ⚠️  HTTP {status_code} on {endpoint} "
                      f"(attempt {attempt + 1}/{MAX_REQUEST_RETRIES}), retrying in {wait}s...")
                time.sleep(wait)
                last_error = result
                continue
            return result
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < MAX_REQUEST_RETRIES - 1:
                wait = (2 ** attempt) * 2
                print(f"  ⚠️  Network error on {endpoint}: {e} "
                      f"(attempt {attempt + 1}/{MAX_REQUEST_RETRIES}), retrying in {wait}s...")
                time.sleep(wait)
                last_error = {"error": True, "message": str(e)}
                continue
            return {"error": True, "message": str(e)}
        except Exception as e:
            return {"error": True, "message": str(e)}

    # 所有重试都耗尽后，返回最后一次错误，避免抛异常中断调用方。
    return last_error or {"error": True, "message": "All retries exhausted"}


def make_multipart_request(endpoint: str, file_field: str, file_path: str,
                           fields: dict[str, str] | None = None) -> dict:
    """使用 multipart/form-data 上传本地文件。"""
    if not os.path.exists(file_path):
        return {"error": True, "message": f"File does not exist: {file_path}"}

    boundary = f"----BorgriseBoundary{int(time.time() * 1000)}"
    body = bytearray()

    for key, value in (fields or {}).items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    filename = os.path.basename(file_path)
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    with open(file_path, "rb") as file_obj:
        body.extend(file_obj.read())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    try:
        headers = _apply_auth_header(headers)
    except Exception as exc:
        return {"error": True, "message": str(exc)}

    last_error: dict | None = None
    for attempt in range(MAX_REQUEST_RETRIES):
        try:
            req = urllib.request.Request(
                f"{BASE_URL}{endpoint}",
                data=bytes(body),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as response:
                result = json.loads(response.read().decode("utf-8"))
                if is_quota_insufficient(result):
                    return _quota_error_response(result, status_code=getattr(response, "status", None))
                return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            parsed = _safe_json_loads(error_body)
            if e.code == QUOTA_INSUFFICIENT_STATUS_CODE or is_quota_insufficient(parsed) or is_quota_insufficient(error_body):
                return _quota_error_response(parsed or error_body, status_code=e.code, fallback_message=error_body)
            retryable = e.code in RETRYABLE_HTTP_CODES
            if retryable and attempt < MAX_REQUEST_RETRIES - 1:
                wait = (2 ** attempt) * 2
                print(f"  ⚠️  HTTP {e.code} (upload) "
                      f"(attempt {attempt + 1}/{MAX_REQUEST_RETRIES}), retrying in {wait}s...")
                time.sleep(wait)
                last_error = {"error": True, "status_code": e.code, "message": error_body}
                continue
            return {"error": True, "status_code": e.code, "message": error_body}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < MAX_REQUEST_RETRIES - 1:
                wait = (2 ** attempt) * 2
                print(f"  ⚠️  Network error (upload): {e} "
                      f"(attempt {attempt + 1}/{MAX_REQUEST_RETRIES}), retrying in {wait}s...")
                time.sleep(wait)
                last_error = {"error": True, "message": str(e)}
                continue
            return {"error": True, "message": str(e)}
        except Exception as e:
            return {"error": True, "message": str(e)}

    return last_error or {"error": True, "message": "All upload retries exhausted"}


def poll_task(task_id: str, timeout: int | None = None, *, default_timeout: int | None = None) -> dict:
    """轮询任务状态，直到完成、失败或超时。

    参数：
        task_id: 要轮询的 Borgrise task ID。
        timeout: 本次调用显式指定的最大等待秒数，优先级最高。
        default_timeout: 业务类型默认等待秒数；视频、图片、视频分析分别传入不同值。
                 未传时按视频任务处理，默认最多等待 1 小时。
    """
    business_default = default_timeout if default_timeout is not None else VIDEO_POLL_TIMEOUT
    effective_timeout = timeout if timeout is not None else _effective_poll_timeout(business_default)
    start_time = time.time()
    last_status = None
    last_error: dict | None = None
    consecutive_status_errors = 0

    while time.time() - start_time < effective_timeout:
        result = make_request(f"/task/{task_id}/status", method="GET")

        if result.get("error"):
            if _is_recoverable_status_poll_error(result) and consecutive_status_errors < STATUS_POLL_ERROR_RECOVERY_ATTEMPTS:
                consecutive_status_errors += 1
                last_error = result
                print(
                    f"Task {task_id}: status query error, retrying poll "
                    f"({consecutive_status_errors}/{STATUS_POLL_ERROR_RECOVERY_ATTEMPTS}): "
                    f"{result.get('message') or result}"
                )
                time.sleep(POLL_INTERVAL)
                continue
            return result

        consecutive_status_errors = 0
        last_error = None
        status = result.get("data", result).get("status", "UNKNOWN")
        normalized_status = str(status).upper()

        if status != last_status:
            print(f"Task {task_id}: {status}")
            last_status = status

        if normalized_status == "COMPLETED":
            return result
        elif normalized_status == "FAILED":
            return {
                "error": True,
                "task_id": task_id,
                "status": "FAILED",
                "message": result.get("data", result).get("error", "Task failed"),
                "details": result
            }

        time.sleep(POLL_INTERVAL)

    return {
        "error": True,
        "task_id": task_id,
        "message": f"Polling timeout after {effective_timeout} seconds",
        "last_status": last_status,
        "last_error": last_error,
    }


def _is_recoverable_status_poll_error(result: dict) -> bool:
    if result.get("quota_insufficient") or result.get("non_retryable"):
        return False
    status_code = result.get("status_code")
    if status_code in {401, QUOTA_INSUFFICIENT_STATUS_CODE}:
        return False
    return status_code is None or status_code in RETRYABLE_HTTP_CODES


def craft_video_prompt(product_description: str, style: str = "cinematic") -> str:
    """根据商品描述拼出更详细的视频生成 prompt。"""
    base_prompt = product_description

    if style == "cinematic":
        motion = "slow cinematic camera movement orbiting around the product, gentle zoom in to highlight details"
        atmosphere = "soft warm lighting casting subtle shadows, elegant and premium product showcase"
    elif style == "dramatic":
        motion = "dynamic camera sweep, dramatic angle changes"
        atmosphere = "bold lighting with strong shadows, impactful commercial presentation"
    else:
        motion = "smooth camera movement showcasing the product"
        atmosphere = "professional product video aesthetic"

    return f"{base_prompt}, {motion}, {atmosphere}, smooth motion, high-end commercial product video"


def craft_image_prompt(product_description: str, scene: str = "studio") -> str:
    """根据商品描述和场景拼出图片生成 prompt。"""
    scene_styles = {
        "studio": "on a clean white surface, soft studio lighting from above, professional product photography",
        "lifestyle": "in an elegant lifestyle setting, natural window light, aspirational aesthetic",
        "flatlay": "flat lay composition, overhead view, clean arrangement, Instagram-worthy",
        "hero": "hero shot, front view, dramatic lighting, premium showcase"
    }

    scene_desc = scene_styles.get(scene, scene_styles["studio"])
    return f"{product_description}, {scene_desc}, high resolution, no watermark"


def extract_task_id(result: dict) -> str | None:
    """从多种响应字段风格中提取 task ID。"""
    # 注意：result.get("data", result) 在 "data": null（键存在但值为 None）时会返回 None，
    # 之后 data.get(...) 会抛 'NoneType' object has no attribute 'get'。
    # content-app SmartPPT 在附件解析失败等场景会返回 data: null，这里必须兜底。
    data = result.get("data")
    data = data if isinstance(data, dict) else result
    return data.get("taskId") or data.get("task_id") or result.get("task_id") or result.get("taskId")


def extract_video_url(result: dict) -> str | None:
    """从轮询/API 响应的多种结构中提取视频 URL。"""
    final_data = result.get("data")
    final_data = final_data if isinstance(final_data, dict) else result
    result_obj = final_data.get("result")
    result_obj = result_obj if isinstance(result_obj, dict) else {}
    return (
        result_obj.get("video_url")
        or result_obj.get("url")
        or final_data.get("video_url")
        or final_data.get("url")
    )


def verify_video_duration(video_url: str, expected_duration: int,
                          tolerance: int = 2) -> dict:
    """使用 ffprobe 尽力校验视频实际时长。

    返回 dict 包含：
      - verified: 是否成功运行 ffprobe。
      - actual_duration: 实测视频时长，单位秒。
      - within_tolerance: 实测时长是否落在容忍区间内。
      - verdict: "PASS" | "FAIL" | "SKIP"。
      - warning: ffprobe 不可用或失败时的说明。

    这是 best-effort 校验：ffprobe 未安装或 URL 不可访问时返回 warning，而不是
    error。调用方应把 FAIL 判定视为生成缺陷。
    """
    import shutil
    import subprocess

    if not shutil.which("ffprobe"):
        return {
            "verified": False,
            "verdict": "SKIP",
            "warning": "ffprobe not available — install ffmpeg to enable duration verification",
            "expected_duration": expected_duration,
        }

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_url,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {
                "verified": False,
                "verdict": "SKIP",
                "warning": f"ffprobe exited with code {result.returncode}: {result.stderr.strip()[:200]}",
                "expected_duration": expected_duration,
            }

        actual = float(result.stdout.strip())
        diff = abs(actual - expected_duration)
        within = diff <= tolerance

        return {
            "verified": True,
            "verdict": "PASS" if within else "FAIL",
            "actual_duration": round(actual, 2),
            "expected_duration": expected_duration,
            "difference": round(diff, 2),
            "within_tolerance": within,
        }
    except Exception as exc:
        return {
            "verified": False,
            "verdict": "SKIP",
            "warning": f"Duration verification error: {exc}",
            "expected_duration": expected_duration,
        }


def extract_uploaded_url(result: dict) -> str | None:
    """从 Borgrise 上传接口的多种响应结构中提取文件 URL。"""
    data = result.get("data", result)
    if isinstance(data, str):
        return data
    return (
        data.get("url")
        or data.get("fileUrl")
        or data.get("file_url")
        or data.get("imageUrl")
        or data.get("image_url")
        or data.get("path")
        or result.get("url")
    )


def extract_asset_id(result: dict) -> str | None:
    """从多种响应结构中提取第三方数字人资产 ID。"""
    data = result.get("data", result)
    return (
        data.get("assetId")
        or data.get("asset_id")
        or data.get("thirdAssetId")
        or data.get("third_asset_id")
        or result.get("assetId")
        or result.get("thirdAssetId")
    )


def upload_file(file_path: str) -> dict:
    """上传本地文件到 Borgrise，并返回可被后续接口引用的公开 URL。"""
    print(f"\n{'='*60}")
    print("POST /api/upload")
    print(f"{'='*60}")
    print(f"File: {file_path}")
    print(f"{'='*60}\n")

    result = make_multipart_request("/upload", "file", file_path)
    if result.get("error"):
        return result

    uploaded_url = extract_uploaded_url(result)
    if not uploaded_url:
        return {"error": True, "message": "No uploaded URL in response", "response": result}

    return {
        "success": True,
        "endpoint": "/api/upload",
        "url": uploaded_url,
        "raw_response": result,
    }


def create_virtual_human_asset(asset_name: str,
                               image_url: str | None = None,
                               image_file: str | None = None,
                               description: str = "",
                               sex: str = "female",
                               age: str = "20",
                               price: float = 0.5,
                               visibility: int = 0) -> dict:
    """创建虚拟人资产，并返回 ``asset://`` 引用。"""
    if not image_url and not image_file:
        return {"error": True, "message": "Provide either image_url or image_file"}

    source_url = image_url
    upload_result = None
    if image_file:
        upload_result = upload_file(image_file)
        if upload_result.get("error"):
            return upload_result
        source_url = upload_result["url"]

    create_third_data = {
        "assetName": asset_name,
        "description": description or asset_name,
        "imageUrl": source_url,
    }

    print(f"\n{'='*60}")
    print("POST /api/asset/virtual-human-asset")
    print(f"{'='*60}")
    print(f"Asset name: {asset_name}")
    print(f"Image URL: {source_url}")
    print(f"{'='*60}\n")

    third_result = make_request("/asset/virtual-human-asset", create_third_data)
    if third_result.get("error"):
        return third_result

    third_asset_id = extract_asset_id(third_result)
    if not third_asset_id:
        return {
            "error": True,
            "message": "No assetId/thirdAssetId in /asset/virtual-human-asset response",
            "response": third_result,
        }

    asset_record_data = {
        "assetType": "xnszr",
        "assetSource": "upload",
        "name": asset_name,
        "sex": sex,
        "age": age,
        "price": price,
        "description": description or asset_name,
        "refrenceUrl": source_url,
        "thirdAssetId": third_asset_id,
        "visibility": visibility,
    }

    print(f"\n{'='*60}")
    print("POST /api/asset/create")
    print(f"{'='*60}")
    print("Asset type: xnszr")
    print(f"Third asset ID: {third_asset_id}")
    print(f"{'='*60}\n")

    record_result = make_request("/asset/create", asset_record_data)
    if record_result.get("error"):
        return {
            "error": True,
            "message": "Virtual human third asset was created, but /asset/create failed",
            "third_asset_id": third_asset_id,
            "details": record_result,
        }

    return {
        "success": True,
        "endpoint": "/api/asset/virtual-human-asset + /api/asset/create",
        "asset_type": "xnszr",
        "asset_name": asset_name,
        "image_url": source_url,
        "third_asset_id": third_asset_id,
        "asset_reference": f"asset://{third_asset_id}",
        "upload": upload_result,
        "raw_response": {
            "virtual_human_asset": third_result,
            "asset_create": record_result,
        },
    }


def resolve_asset_urls(asset_ids: list[str]) -> dict:
    """通过 Borgrise 前端端点把资产 ID 解析成参考 URL。"""
    if not asset_ids:
        return {"error": True, "message": "At least one asset id is required"}

    clean_ids = [asset_id.replace("asset://", "") for asset_id in asset_ids]
    result = make_request("/asset/refrence-urls", clean_ids)
    if result.get("error"):
        return result

    return {
        "success": True,
        "endpoint": "/api/asset/refrence-urls",
        "asset_ids": clean_ids,
        "raw_response": result,
    }


def _extract_smart_ppt_project_id(*payloads: Any) -> int | None:
    """从 SmartPPT 响应中提取项目 ID。"""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        data = payload.get("data", payload)
        candidates = [
            data.get("smartPptProjectId") if isinstance(data, dict) else None,
            data.get("smart_ppt_project_id") if isinstance(data, dict) else None,
            payload.get("smartPptProjectId"),
            payload.get("smart_ppt_project_id"),
        ]
        for candidate in candidates:
            if candidate is None or candidate == "":
                continue
            try:
                return int(candidate)
            except (TypeError, ValueError):
                continue
    return None


def _task_result_payload(poll_result: dict) -> Any:
    data = poll_result.get("data", poll_result)
    if not isinstance(data, dict):
        return data
    if "result" in data:
        return data["result"]
    for key in ("resultData", "result_data", "data"):
        if key in data:
            return data[key]
    return data


def _walk_smart_ppt_payloads(payload: Any) -> list[dict[str, Any]]:
    """按由外到内顺序展开 SmartPPT 常见响应包装层。"""
    visited: set[int] = set()
    items: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if not isinstance(value, dict) or id(value) in visited:
            return
        visited.add(id(value))
        items.append(value)
        for key in ("data", "result", "resultData", "result_data"):
            if key in value:
                walk(value[key])

    walk(payload)
    return items


def _smart_ppt_value(payload: Any, *keys: str) -> Any:
    """从 SmartPPT 多层响应里读取第一个非空字段。"""
    for item in _walk_smart_ppt_payloads(payload):
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
    return None


def _smart_ppt_error(endpoint: str, result: dict | None, *, task_id: str | None = None, project_id: int | None = None) -> dict:
    payload = result or {}
    message = _extract_error_message(payload, "SmartPPT task failed")
    response = {
        "error": True,
        "success": False,
        "endpoint": f"/api{endpoint}",
        "task_id": task_id or extract_task_id(payload),
        "smart_ppt_project_id": project_id or _extract_smart_ppt_project_id(payload),
        "message": message,
        "raw_response": payload,
    }
    if is_quota_insufficient(payload):
        response.update(
            {
                "quota_insufficient": True,
                "non_retryable": True,
                "status_code": payload.get("status_code", QUOTA_INSUFFICIENT_STATUS_CODE),
            }
        )
    return response


def _submit_and_poll_smart_ppt(endpoint: str, request_data: dict) -> tuple[dict, dict] | dict:
    result = make_request(endpoint, request_data)
    if not isinstance(result, dict):
        # content-app SmartPPT 偶发返回空/非字典（例如附件解析失败、网关兜底），
        # 不能直接 result.get()，否则会抛 'NoneType' object has no attribute 'get'，
        # 让上层只看到崩溃而不是可读错误。这里归一成结构化错误。
        return _smart_ppt_error(
            endpoint,
            {"message": "SmartPPT 提交返回空响应或非 JSON 结果", "response": result},
        )
    if result.get("error") or result.get("success") is False:
        return _smart_ppt_error(endpoint, result)
    task_id = extract_task_id(result)
    project_id = _extract_smart_ppt_project_id(result)
    if not task_id:
        return _smart_ppt_error(endpoint, {"message": "No taskId in SmartPPT response", "response": result}, project_id=project_id)

    poll_result = poll_task(task_id, default_timeout=PPT_POLL_TIMEOUT)
    if poll_result.get("error") or poll_result.get("success") is False:
        return _smart_ppt_error(endpoint, poll_result, task_id=task_id, project_id=project_id)
    return result, poll_result


def generate_ppt_summary(
    topic: str,
    ppt_style: str,
    file_urls: list[str],
    smart_ppt_project_id: int | None = None,
) -> dict:
    """调用智能 PPT 生成大纲接口并轮询结果。"""
    request_data: dict[str, Any] = {
        "topic": topic,
        "pptStyle": ppt_style,
        "fileUrls": file_urls,
    }
    if smart_ppt_project_id is not None:
        request_data["smartPptProjectId"] = smart_ppt_project_id

    endpoint = "/picture/smart-ppt/generatePptSummary"
    print(f"\n{'='*60}")
    print("POST /api/picture/smart-ppt/generatePptSummary")
    print(f"{'='*60}")
    print(f"Topic: {topic}")
    print(f"Style: {ppt_style}")
    print(f"Files: {len(file_urls)}")
    print(f"{'='*60}\n")

    submitted = _submit_and_poll_smart_ppt(endpoint, request_data)
    if isinstance(submitted, dict):
        return submitted
    start_result, poll_result = submitted
    task_result = _task_result_payload(poll_result)
    summary = _smart_ppt_value(task_result, "summary", "outline", "pptSummary") if isinstance(task_result, dict) else str(task_result or "")
    return {
        "success": True,
        "endpoint": "/api/picture/smart-ppt/generatePptSummary",
        "task_id": extract_task_id(start_result),
        "smart_ppt_project_id": _extract_smart_ppt_project_id(start_result, poll_result),
        "summary": summary or "",
        "raw_response": {"start": start_result, "poll": poll_result},
    }


def update_ppt_summary(
    original_outline: str,
    modification_opinion: str,
    smart_ppt_project_id: int,
) -> dict:
    """调用智能 PPT 更新大纲接口并轮询结果。"""
    endpoint = "/picture/smart-ppt/updatePptSummary"
    request_data = {
        "originalOutline": original_outline,
        "smartPptProjectId": smart_ppt_project_id,
        "modificationOpinion": modification_opinion,
    }
    submitted = _submit_and_poll_smart_ppt(endpoint, request_data)
    if isinstance(submitted, dict):
        return submitted
    start_result, poll_result = submitted
    task_result = _task_result_payload(poll_result)
    summary = _smart_ppt_value(task_result, "summary", "outline", "pptSummary") if isinstance(task_result, dict) else str(task_result or "")
    return {
        "success": True,
        "endpoint": "/api/picture/smart-ppt/updatePptSummary",
        "task_id": extract_task_id(start_result),
        "smart_ppt_project_id": _extract_smart_ppt_project_id(start_result, poll_result) or smart_ppt_project_id,
        "summary": summary or "",
        "raw_response": {"start": start_result, "poll": poll_result},
    }


def generate_ppt_content_json(
    original_outline: str,
    ppt_style: str,
    smart_ppt_project_id: int,
) -> dict:
    """调用智能 PPT 大纲转 JSON 接口并轮询结果。"""
    endpoint = "/picture/smart-ppt/generatePptContentToJson"
    request_data = {
        "originalOutline": original_outline,
        "smartPptProjectId": smart_ppt_project_id,
        "pptStyle": ppt_style,
    }
    submitted = _submit_and_poll_smart_ppt(endpoint, request_data)
    if isinstance(submitted, dict):
        return submitted
    start_result, poll_result = submitted
    task_result = _task_result_payload(poll_result)
    if isinstance(task_result, dict):
        content_json = _smart_ppt_value(task_result, "content_json", "contentJson", "json", "pptJson")
    else:
        content_json = task_result
    return {
        "success": True,
        "endpoint": "/api/picture/smart-ppt/generatePptContentToJson",
        "task_id": extract_task_id(start_result),
        "smart_ppt_project_id": _extract_smart_ppt_project_id(start_result, poll_result) or smart_ppt_project_id,
        "content_json": content_json,
        "raw_response": {"start": start_result, "poll": poll_result},
    }


def generate_ppt_image(json_content: str, smart_ppt_project_id: int) -> dict:
    """调用智能 PPT 单页图片生成接口并轮询结果。"""
    endpoint = "/picture/smart-ppt/generatePptImage"
    request_data = {
        "jsonContent": json_content,
        "smartPptProjectId": smart_ppt_project_id,
    }
    submitted = _submit_and_poll_smart_ppt(endpoint, request_data)
    if isinstance(submitted, dict):
        return submitted
    start_result, poll_result = submitted
    task_result = _task_result_payload(poll_result)
    if isinstance(task_result, str):
        image_url = task_result
    elif isinstance(task_result, dict):
        image_url = _smart_ppt_value(task_result, "image_url", "imageUrl", "url", "ppt_image_url", "pptImageUrl")
    else:
        image_url = None
    return {
        "success": True,
        "endpoint": "/api/picture/smart-ppt/generatePptImage",
        "task_id": extract_task_id(start_result),
        "smart_ppt_project_id": _extract_smart_ppt_project_id(start_result, poll_result) or smart_ppt_project_id,
        "image_url": image_url,
        "raw_response": {"start": start_result, "poll": poll_result},
    }


def generate_ppt_file(file_urls: list[str], smart_ppt_project_id: int) -> dict:
    """调用智能 PPT 图片集合转 PPT 文件接口并轮询结果。"""
    endpoint = "/picture/smart-ppt/generatePptFile"
    request_data = {
        "fileUrls": file_urls,
        "smartPptProjectId": smart_ppt_project_id,
    }
    submitted = _submit_and_poll_smart_ppt(endpoint, request_data)
    if isinstance(submitted, dict):
        return submitted
    start_result, poll_result = submitted
    task_result = _task_result_payload(poll_result)
    result_data = task_result if isinstance(task_result, dict) else {}
    return {
        "success": True,
        "endpoint": "/api/picture/smart-ppt/generatePptFile",
        "task_id": extract_task_id(start_result),
        "smart_ppt_project_id": _extract_smart_ppt_project_id(start_result, poll_result) or smart_ppt_project_id,
        "ppt_url": _smart_ppt_value(result_data, "ppt_url", "pptUrl", "url", "fileUrl"),
        "filename": _smart_ppt_value(result_data, "filename", "fileName", "name"),
        "slide_count": _smart_ppt_value(result_data, "slide_count", "slideCount", "pageCount"),
        "raw_response": {"start": start_result, "poll": poll_result},
    }


def image_to_video(image_url: str, prompt: str | None = None,
                   duration: int = 10, ratio: str = "9:16",
                   model: str = DEFAULT_VIDEO_MODEL,
                   size: str = "720p",
                   sound: str = "on",
                   video_count: int = 1,
                   product_description: str | None = None,
                   auto_poll: bool = True) -> dict:
    """根据单张图片生成视频。"""

    validation_error = validate_ratio(ratio) or validate_video_duration(duration, model)
    if validation_error:
        return validation_error

    if not prompt and product_description:
        prompt = craft_video_prompt(product_description)
    elif not prompt:
        prompt = "Professional product showcase video, smooth camera movement, elegant presentation"

    request_data = {
        "image_url": image_url,
        "prompt": prompt,
        "negative_prompt": "blurry, distorted, low quality, watermark, text overlay, shaky camera",
        "model": model,
        "duration": duration,
        "ratio": ratio,
        "size": size,
        "sound": sound,
        "videoCount": video_count,
        "seed": None
    }

    print(f"\n{'='*60}")
    print("POST /api/video/image-to-video")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Ratio: {ratio}")
    print(f"Size: {size}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request("/video/image-to-video", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    if not auto_poll:
        return {
            "success": True,
            "task_id": task_id,
            "status": result.get("status", "PENDING"),
            "endpoint": "/api/video/image-to-video",
            "model": model,
            "raw_response": result
        }

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=VIDEO_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    video_url = extract_video_url(poll_result)

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/video/image-to-video",
        "model": model,
        "video_url": video_url,
        "raw_response": poll_result
    }


def two_image_to_video(first_frame_image_url: str,
                       last_frame_image_url: str,
                       prompt: str | None = None,
                       duration: int = 10,
                       ratio: str = "9:16",
                       size: str = "720p",
                       model: str = DEFAULT_VIDEO_MODEL,
                       sound: str = "on",
                       video_count: int = 1,
                       auto_poll: bool = True) -> dict:
    """根据首帧图和尾帧图生成视频。"""

    validation_error = validate_ratio(ratio) or validate_video_duration(duration, model)
    if validation_error:
        return validation_error

    request_data = {
        "first_frame_image_url": first_frame_image_url,
        "last_frame_image_url": last_frame_image_url,
        "prompt": prompt or "",
        "model": model,
        "duration": duration,
        "ratio": ratio,
        "size": size,
        "sound": sound,
        "videoCount": video_count
    }

    print(f"\n{'='*60}")
    print("POST /api/video/two-image-to-video")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Ratio: {ratio}")
    print(f"Size: {size}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request("/video/two-image-to-video", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    if not auto_poll:
        return {
            "success": True,
            "task_id": task_id,
            "status": result.get("status", "PENDING"),
            "endpoint": "/api/video/two-image-to-video",
            "model": model,
            "raw_response": result
        }

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=VIDEO_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    video_url = extract_video_url(poll_result)

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/video/two-image-to-video",
        "model": model,
        "video_url": video_url,
        "raw_response": poll_result
    }


def text_to_video(prompt: str,
                  duration: int = 10,
                  ratio: str = "9:16",
                  size: str = "720p",
                  model: str = DEFAULT_VIDEO_MODEL,
                  sound: str = "on",
                  video_count: int = 1,
                  auto_poll: bool = True) -> dict:
    """根据纯文本 prompt 生成视频。"""

    validation_error = validate_ratio(ratio) or validate_video_duration(duration, model)
    if validation_error:
        return validation_error

    request_data = {
        "prompt": prompt,
        "model": model,
        "duration": duration,
        "ratio": ratio,
        "size": size,
        "sound": sound,
        "videoCount": video_count
    }

    print(f"\n{'='*60}")
    print("POST /api/video/text-to-video")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Ratio: {ratio}")
    print(f"Size: {size}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request("/video/text-to-video", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    if not auto_poll:
        return {
            "success": True,
            "task_id": task_id,
            "status": result.get("status", "PENDING"),
            "endpoint": "/api/video/text-to-video",
            "model": model,
            "raw_response": result
        }

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=VIDEO_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    video_url = extract_video_url(poll_result)

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/video/text-to-video",
        "model": model,
        "video_url": video_url,
        "raw_response": poll_result
    }


def reference_mode_video(prompt: str,
                         image_urls: list[str] | None = None,
                         video_urls: list[str] | None = None,
                         audio_urls: list[str] | None = None,
                         duration: int = 10,
                         ratio: str = "9:16",
                         size: str = "720p",
                         model: str = DEFAULT_VIDEO_MODEL,
                         sound: str = "on",
                         video_count: int = 1,
                         auto_poll: bool = True) -> dict:
    """根据多模态参考素材生成视频。

    这对应 Borgrise 测试前端的“reference mode”调用。适用于上传图片、音频、视频
    作为参考素材的场景，而不是单纯把一张图当作首帧。
    """

    validation_error = validate_ratio(ratio) or validate_video_duration(duration, model)
    if validation_error:
        return validation_error

    request_data = {
        "prompt": prompt,
        "imageUrls": image_urls or [],
        "videoUrls": video_urls or [],
        "audioUrls": audio_urls or [],
        "duration": duration,
        "ratio": ratio,
        "sound": sound,
        "model": model,
        "size": size,
        "videoCount": video_count
    }

    print(f"\n{'='*60}")
    print("POST /api/video/reference-mode-video")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Ratio: {ratio}")
    print(f"Size: {size}")
    print(f"Images: {len(image_urls or [])}")
    print(f"Videos: {len(video_urls or [])}")
    print(f"Audio: {len(audio_urls or [])}")
    print(f"Prompt preview: {prompt[:180]}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request("/video/reference-mode-video", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    if not auto_poll:
        return {
            "success": True,
            "task_id": task_id,
            "status": result.get("status", "PENDING"),
            "endpoint": "/api/video/reference-mode-video",
            "model": model,
            "raw_response": result
        }

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=VIDEO_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    video_url = extract_video_url(poll_result)

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/video/reference-mode-video",
        "model": model,
        "video_url": video_url,
        "raw_response": poll_result
    }


def edit_video(ref_video: str,
               prompt: str | None = None,
               ref_image: str | None = None,
               duration: int = 10,
               ratio: str = "9:16",
               size: str = "720p",
               model: str = DEFAULT_VIDEO_MODEL,
               sound: str = "on",
               video_count: int = 1,
               auto_poll: bool = True) -> dict:
    """根据参考视频和可选参考图编辑生成新视频。"""

    validation_error = validate_ratio(ratio) or validate_video_duration(duration, model)
    if validation_error:
        return validation_error

    request_data = {
        "refVideo": ref_video,
        "refImage": ref_image or "",
        "prompt": prompt or "",
        "model": model,
        "duration": duration,
        "ratio": ratio,
        "size": size,
        "sound": sound,
        "videoCount": video_count
    }

    print(f"\n{'='*60}")
    print("POST /api/video/edit-video")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Ratio: {ratio}")
    print(f"Size: {size}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request("/video/edit-video", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    if not auto_poll:
        return {
            "success": True,
            "task_id": task_id,
            "status": result.get("status", "PENDING"),
            "endpoint": "/api/video/edit-video",
            "model": model,
            "raw_response": result
        }

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=VIDEO_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    video_url = extract_video_url(poll_result)

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/video/edit-video",
        "model": model,
        "video_url": video_url,
        "raw_response": poll_result
    }


def _strip_prompt_timecode(prompt: str) -> str:
    return re.sub(r"^\s*\d+\s*-\s*\d+\s*s[:：]\s*", "", prompt).strip()


def _extract_primary_dialogue(prompt: str) -> str | None:
    cleaned = _strip_prompt_timecode(prompt)
    markers = ["女播主说：", "女播主说:", "旁白说：", "旁白说:", "女1说：", "女1说:"]
    for marker in markers:
        if marker in cleaned:
            dialogue = cleaned.split(marker, 1)[1].strip()
            return dialogue.strip(" \"'") if dialogue else None
    return None


def build_native_audio_prompt(prompt: str) -> str:
    """把用户视频 prompt 包装成“口播优先”的提示合同。

    排查发现，如果一开始塞入大量技术说明、时间码和镜头语法，生成音频的前几秒
    容易被污染。因此这里把真正要朗读的台词放在第一行，非朗读约束尽量短。
    """
    cleaned = _strip_prompt_timecode(prompt)
    dialogue = _extract_primary_dialogue(prompt)

    if dialogue:
        parts = [
            dialogue,
            "上面第一行是唯一允许朗读的中文口播台词；不要朗读本句或任何说明文字。",
        ]
    else:
        parts = [
            "生成一条自然中文口播短视频，使用简短真实口语，不要朗读提示词说明。",
        ]
    parts.extend([
        "音画同步，不要字幕，不要念出时间码、标签名或技术要求。",
        "保持参考素材中的产品外观和人物身份一致，画面自然真实，口型同步，语音像真人口播，不要AI感乱码发音。",
        f"画面内容：{cleaned}",
    ])
    return "\n".join(parts)


def native_audio_reference_video(prompt: str,
                                 image_urls: list[str] | None = None,
                                 video_urls: list[str] | None = None,
                                 audio_urls: list[str] | None = None,
                                 duration: int = 10,
                                 ratio: str = "9:16",
                                 size: str = "720p",
                                 model: str = DEFAULT_VIDEO_MODEL,
                                 sound: str = "on",
                                 video_count: int = 1,
                                 reference_video_fn=reference_mode_video) -> dict:
    """生成带模型原生中文音频的 reference-mode 视频。"""
    return reference_video_fn(
        prompt=build_native_audio_prompt(prompt),
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
        duration=duration,
        ratio=ratio,
        size=size,
        model=model,
        sound=sound,
        video_count=video_count,
    )


def extend_video(video_url: str, duration: int = 10,
                 model: str = DEFAULT_VIDEO_MODEL,
                 prompt: str | None = None,
                 ratio: str = "9:16",
                 size: str = "720p",
                 sound: str = "on",
                 auto_poll: bool = True,
                 max_total_duration: int | None = None,
                 current_cumulative_duration: int = 0) -> dict:
    """按正确 API 格式延展已有视频。

    extend-video API 要求：
    - refVideoList：视频 URL 数组。
    - prompt：必须包含 "@filename" 来引用被延展的视频。

    时长安全保护（关键）：
    - max_total_duration: 设置后，如果 current_cumulative_duration + duration
      会超过总时长上限，本函数会拒绝继续 extend。
    - current_cumulative_duration: 之前片段已经累计生成的总时长。比如首段 10 秒后
      第一次 extend 传 10，第二次 extend 传 20。
    - 示例：目标 30 秒时，第一次 extend 为 (cum=10, max=30)，第二次为
      (cum=20, max=30)。第三次 (cum=30, max=30) 会被拒绝，因为 30+10 > 30。
    """

    # ---- 累计时长保护 ---------------------------------------------------
    if max_total_duration is not None:
        would_reach = current_cumulative_duration + duration
        if would_reach > max_total_duration:
            return {
                "error": True,
                "message": (
                    f"Refusing extend: would exceed max total duration. "
                    f"Cumulative so far: {current_cumulative_duration}s, "
                    f"requested extend: {duration}s → would reach "
                    f"{would_reach}s, max allowed: {max_total_duration}s."
                ),
                "current_cumulative_duration": current_cumulative_duration,
                "requested_extend_duration": duration,
                "would_reach": would_reach,
                "max_total_duration": max_total_duration,
            }
    # ------------------------------------------------------------------

    validation_error = validate_ratio(ratio) or validate_video_duration(duration, model)
    if validation_error:
        return validation_error

    # 从 URL 中取文件名，供 @filename 引用使用。
    filename = video_url.split("/")[-1]
    if not prompt:
        prompt = f"将@{filename}向后延伸，延长内容为延续之前的视频内容"

    # extend-video prompt 必须包含 @filename 引用，否则供应商可能无法定位参考视频。
    if f"@{filename}" not in prompt:
        prompt = f"将@{filename}向后延伸，延长内容为" + prompt

    request_data = {
        "refVideoList": [video_url],
        "prompt": prompt,
        "model": model,
        "duration": duration,
        "size": size,
        "ratio": ratio,
        "sound": sound,
        "videoCount": "1"
    }

    print(f"\n{'='*60}")
    print("POST /api/video/extend-video")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Size: {size}")
    print(f"Sound: {sound}")
    print(f"Reference: {filename}")
    print(f"Prompt preview: {prompt[:180]}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request("/video/extend-video", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    if not auto_poll:
        return {
            "success": True,
            "task_id": task_id,
            "status": result.get("status", "PENDING"),
            "endpoint": "/api/video/extend-video",
            "model": model,
            "raw_response": result
        }

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=VIDEO_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    extended_video_url = extract_video_url(poll_result)

    new_cumulative = current_cumulative_duration + duration

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/video/extend-video",
        "model": model,
        "duration": duration,
        "cumulative_before": current_cumulative_duration,
        "cumulative_after": new_cumulative,
        "video_url": extended_video_url,
        "raw_response": poll_result
    }


def merge_videos(video_urls: list[str],
                 model: str = DEFAULT_VIDEO_MODEL,
                 duration: int = 30,
                 size: str = "1080p") -> dict:
    """把多个视频片段合并成一个交付视频。

    Swagger 中 VideoMergeRequest 使用 ``videoUrls``。旧的 snake_case
    ``video_urls`` 字段会被后端拒绝；项目归属已由 content-app 后端按登录态处理。
    """

    if len(video_urls) < 2:
        return {"error": True, "message": "At least two video URLs are required for merge"}

    request_data = {
        "videoUrls": video_urls
    }

    print(f"\n{'='*60}")
    print("POST /api/video/merge")
    print(f"{'='*60}")
    print(f"Videos: {len(video_urls)}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request(
        "/video/merge",
        request_data,
        custom_headers=headers,
        request_timeout=VIDEO_MERGE_REQUEST_TIMEOUT,
    )

    if result.get("error"):
        return result

    data = result.get("data", result)
    video_url = data.get("video_url") or data.get("url") or result.get("video_url") or result.get("url")

    return {
        "success": bool(result.get("success", True)),
        "task_id": extract_task_id(result),
        "status": result.get("status", "COMPLETED"),
        "endpoint": "/api/video/merge",
        "model": model,
        "video_url": video_url,
        "raw_response": result
    }


def extract_media_links(text: str) -> dict:
    """调用 content-app 从文本中识别媒体链接。

    content-app 的 ``/api/creative/extractMediaLinks`` 是同步轻量接口，不创建任务、
    不扣配额。这里仍然透传当前用户 Authorization，保证网关合同一致。
    """
    normalized_text = text.strip()
    if not normalized_text:
        return {"error": True, "message": "text is required"}

    result = make_request("/creative/extractMediaLinks", {"text": normalized_text})
    if result.get("error"):
        return result

    data = result.get("data", [])
    links = [str(item) for item in data if item] if isinstance(data, list) else []
    return {
        "success": bool(result.get("success", True)),
        "endpoint": "/api/creative/extractMediaLinks",
        "links": links,
        "raw_response": result,
    }


def batch_decompose_video_to_storyboard(
        video_urls: list[str],
        generation_dialog_id: int | None = None,
        parent_generation_dialog_id: int | None = None,
) -> dict:
    """调用 content-app 批量视频拆解接口并轮询任务结果。"""
    clean_urls = [url.strip() for url in video_urls if url and url.strip()]
    if len(clean_urls) < 2:
        return {"error": True, "message": "At least two video URLs are required for batch decompose"}

    request_data: dict[str, Any] = {"videoUrls": clean_urls}
    if generation_dialog_id is not None:
        request_data["generationDialogId"] = generation_dialog_id
    if parent_generation_dialog_id is not None:
        request_data["parentGenerationDialogId"] = parent_generation_dialog_id

    headers = get_headers(model="gemini-3-flash-preview", bill_type=1, duration=1, size="all")
    result = make_request("/creative/batch_decompose_video_to_storyboard", request_data, custom_headers=headers)
    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    poll_result = result
    if task_id:
        poll_result = poll_task(task_id, default_timeout=VIDEO_ANALYSIS_POLL_TIMEOUT)
        if poll_result.get("error"):
            return poll_result

    final_data = poll_result.get("data", poll_result)
    result_data = final_data.get("result", final_data) if isinstance(final_data, dict) else {}
    if not isinstance(result_data, dict):
        result_data = {}
    final_dict = final_data if isinstance(final_data, dict) else {}
    nested_video_payload = result_data.get("video_url") or result_data.get("videoUrl")
    payload_sources = [result_data]
    if isinstance(nested_video_payload, dict):
        payload_sources.append(nested_video_payload)
    payload_sources.append(final_dict)

    def first_payload_value(*keys: str) -> Any:
        for payload in payload_sources:
            for key in keys:
                value = payload.get(key)
                if value:
                    return value
        return None

    analysis_markdown = (
        first_payload_value("batch_video_analysis_markdown", "batchVideoAnalysisMarkdown")
        or ""
    )
    generation_prompt = (
        first_payload_value("batch_video_generation_prompt", "batchVideoGenerationPrompt")
        or ""
    )
    storyboards = first_payload_value("storyboards", "segments") or []
    if not isinstance(storyboards, list):
        storyboards = []
    if not storyboards and (analysis_markdown or generation_prompt):
        storyboards = [
            {
                "video_urls": clean_urls,
                "analysis_markdown": analysis_markdown,
                "generation_prompt": generation_prompt,
            }
        ]

    return {
        "success": bool(poll_result.get("success", result.get("success", True))),
        "task_id": task_id,
        "status": final_dict.get("status", poll_result.get("status", "COMPLETED")),
        "endpoint": "/api/creative/batch_decompose_video_to_storyboard",
        "video_urls": clean_urls,
        "storyboards": storyboards,
        "batch_video_analysis_markdown": analysis_markdown,
        "batch_video_generation_prompt": generation_prompt,
        "raw_response": poll_result,
    }


def analyze_video_flaws(
        merged_video_url: str,
        scene_videos: list[dict],
        scene_packages: list[dict] | None = None,
        brief: dict[str, Any] | None = None,
        materials: list[dict] | None = None,
        user_feedback: str | None = None,
        checks: list[str] | None = None,
        platform: str | None = None,
        ratio: str | None = None,
        size: str | None = None,
        generation_dialog_id: int | None = None,
        parent_generation_dialog_id: int | None = None,
) -> dict:
    """调用 content-app 视频穿帮分析接口并轮询任务结果。"""
    if not merged_video_url:
        return {"error": True, "message": "merged_video_url is required"}
    if not scene_videos:
        return {"error": True, "message": "scene_videos is required"}

    request_data: dict[str, Any] = {
        "merged_video_url": merged_video_url,
        "scene_videos": scene_videos,
        "scene_packages": scene_packages or [],
        "brief": brief or {},
        "materials": materials or [],
        "user_feedback": user_feedback or "",
    }
    if checks:
        request_data["checks"] = checks
    if platform:
        request_data["platform"] = platform
    if ratio:
        request_data["ratio"] = ratio
    if size:
        request_data["size"] = size
    if generation_dialog_id is not None:
        request_data["generationDialogId"] = generation_dialog_id
    if parent_generation_dialog_id is not None:
        request_data["parentGenerationDialogId"] = parent_generation_dialog_id

    headers = get_headers(model="gemini-3-flash-preview", bill_type=1, duration=1, size="all")
    result = make_request("/creative/analyze_video_flaws", request_data, custom_headers=headers)
    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    poll_result = result
    if task_id:
        poll_result = poll_task(task_id, default_timeout=VIDEO_ANALYSIS_POLL_TIMEOUT)
        if poll_result.get("error"):
            return poll_result

    final_data = poll_result.get("data", poll_result)
    result_data = final_data.get("result", final_data) if isinstance(final_data, dict) else {}
    if not isinstance(result_data, dict):
        result_data = {}
    final_dict = final_data if isinstance(final_data, dict) else {}

    return {
        "success": bool(poll_result.get("success", result.get("success", True))),
        "task_id": task_id,
        "status": final_dict.get("status", poll_result.get("status", "COMPLETED")),
        "endpoint": "/api/creative/analyze_video_flaws",
        "flaw_analysis_markdown": (
            result_data.get("flaw_analysis_markdown")
            or result_data.get("flawAnalysisMarkdown")
            or final_dict.get("flaw_analysis_markdown", "")
        ),
        "issues": result_data.get("issues") or final_dict.get("issues", []),
        "affected_scene_ids": (
            result_data.get("affected_scene_ids")
            or result_data.get("affectedSceneIds")
            or final_dict.get("affected_scene_ids", [])
        ),
        "revision_prompt": (
            result_data.get("revision_prompt")
            or result_data.get("revisionPrompt")
            or final_dict.get("revision_prompt", "")
        ),
        "raw_response": poll_result,
    }


def select_long_video_delivery(segments: list[dict]) -> dict:
    """为基于 extend 的长视频流程选择最终交付物。

    Borgrise extend-video 返回的是累计视频：每次 extend 的结果已经包含之前内容和
    新延展内容。因此最后一个 segment URL 就是完整交付物。如果再把首段和最后
    的累计结果 merge，会重复开头片段，让 60 秒视频变成约 70 秒。
    """
    if not segments:
        return {
            "error": True,
            "message": "No generated segments available for delivery",
        }

    return {
        "video_url": segments[-1].get("video_url"),
        "merge_required": False,
        "merge_urls": [],
        "reason": "extend-video returns cumulative results; use the final extend output directly",
    }


def long_image_to_video(image_url: str, prompt: str | None = None,
                        total_duration: int = 20, segment_duration: int = 10,
                        ratio: str = "9:16", model: str = DEFAULT_VIDEO_MODEL,
                        product_description: str | None = None,
                        size: str = "720p",
                        sound: str = "on",
                        force_long: bool = False,
                        progress_file: str | None = None) -> dict:
    """先生成首段，再反复 extend，得到长视频。

    注意：seedance-2.0 单段最长 10 秒，因此更长视频需要使用 extend-video。
    累计时长追踪用于避免过度生成，最终累计结果会用 ffprobe 做 best-effort 时长校验。
    """

    validation_error = validate_ratio(ratio)
    if validation_error:
        return validation_error

    if segment_duration > SEEDANCE_MAX_SEGMENT_DURATION and model == "seedance-2.0":
        return {
            "error": True,
            "message": f"segment-duration must be <= {SEEDANCE_MAX_SEGMENT_DURATION}s for seedance-2.0",
            "requested_segment_duration": segment_duration,
        }

    if total_duration > SAFE_MAX_LONG_VIDEO_DURATION:
        if not force_long:
            return {
                "error": True,
                "message": (
                    f"Refusing automatic long-image-to-video above "
                    f"{SAFE_MAX_LONG_VIDEO_DURATION}s (requested {total_duration}s). "
                    "Pass --allow-long after the user has confirmed the segment plan "
                    "and final delivery + ffprobe verification."
                ),
                "requested_total_duration": total_duration,
                "safe_max_duration": SAFE_MAX_LONG_VIDEO_DURATION,
            }
        print(
            f"⚠️  WARNING: Generating {total_duration}s video with force_long=True. "
            f"Safe max is {SAFE_MAX_LONG_VIDEO_DURATION}s."
        )

    if total_duration <= segment_duration:
        return image_to_video(
            image_url=image_url,
            prompt=prompt,
            duration=total_duration,
            ratio=ratio,
            model=model,
            product_description=product_description
        )

    # 计算段数：首段由 image-to-video 生成，剩余时长通过 extend-video 延展。
    total_segments = -(-total_duration // segment_duration)  # 向上取整。
    actual_total = total_segments * segment_duration
    if actual_total != total_duration:
        last_segment = total_duration - (total_segments - 1) * segment_duration
        if last_segment <= 0:
            total_segments = total_duration // segment_duration
            last_segment = segment_duration
    else:
        last_segment = segment_duration

    print(f"\n{'='*60}")
    print("LONG IMAGE TO VIDEO WORKFLOW")
    print(f"{'='*60}")
    print(f"Target Duration: {total_duration}s")
    print(f"Segment Duration: {segment_duration}s")
    print(f"Segments: {total_segments}")
    print(f"{'='*60}\n")

    segments = []
    # 长图生视频只有一个用户 prompt；进度文件需要列表形态，便于恢复工具复用统一结构。
    progress_prompts = [prompt or product_description or ""] * total_segments

    # 生成首段。
    first_result = image_to_video(
        image_url=image_url,
        prompt=prompt,
        duration=segment_duration,
        ratio=ratio,
        model=model,
        product_description=product_description,
        auto_poll=True
    )
    if first_result.get("error"):
        return first_result

    current_video_url = first_result.get("video_url")
    segments.append({
        "segment": 1,
        "task_id": first_result.get("task_id"),
        "video_url": current_video_url,
        "endpoint": first_result.get("endpoint"),
        "cumulative_duration": segment_duration,
    })

    # ---- 首段完成后保存进度 -------------------------------------------
    if progress_file:
        try:
            _progress = {
                "total_duration": total_duration,
                "segment_duration": segment_duration,
                "segments_completed": 1,
                "total_segments": total_segments,
                "prompts": progress_prompts,
                "segments": segments,
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            os.makedirs(os.path.dirname(progress_file) or ".", exist_ok=True)
            with open(progress_file, "w") as pf:
                json.dump(_progress, pf, indent=2, ensure_ascii=False)
            print(f"Progress saved: 1/{total_segments} segments → {progress_file}")
        except Exception:
            pass

    elapsed = segment_duration

    # 逐段延展剩余时长。
    for idx in range(2, total_segments + 1):
        print(f"\nExtending segment {idx}/{total_segments}...")

        remaining = total_duration - elapsed
        if remaining <= 0:
            break
        seg_dur = min(segment_duration, remaining)

        extend_prompt = prompt or "延续之前的视频内容，继续保持画面节奏和风格，动态镜头，流畅过渡"

        extend_result = extend_video(
            video_url=current_video_url,
            duration=seg_dur,
            model=model,
            prompt=extend_prompt,
            ratio=ratio,
            size=size,
            sound=sound,
            auto_poll=True,
            max_total_duration=total_duration,
            current_cumulative_duration=elapsed,
        )
        if extend_result.get("error"):
            return {
                "error": True,
                "message": f"Failed on segment {idx}",
                "segments_completed": len(segments),
                "details": extend_result,
                "segments": segments
            }

        current_video_url = extend_result.get("video_url")
        elapsed += seg_dur
        segments.append({
            "segment": idx,
            "task_id": extend_result.get("task_id"),
            "video_url": current_video_url,
            "endpoint": extend_result.get("endpoint"),
            "cumulative_duration": elapsed,
        })

        # ---- 每段完成后保存进度 ----------------------------------------
        if progress_file:
            try:
                _progress = {
                    "total_duration": total_duration,
                    "segment_duration": segment_duration,
                    "segments_completed": len(segments),
                    "total_segments": total_segments,
                    "prompts": progress_prompts,
                    "segments": segments,
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                os.makedirs(os.path.dirname(progress_file) or ".", exist_ok=True)
                with open(progress_file, "w") as pf:
                    json.dump(_progress, pf, indent=2, ensure_ascii=False)
                print(f"Progress saved: {len(segments)}/{total_segments} segments → {progress_file}")
            except Exception:
                pass

    delivery = select_long_video_delivery(segments)
    if delivery.get("error"):
        return delivery

    final_video_url = delivery.get("video_url")
    merge_result = None

    # ---- 最终时长校验 ---------------------------------------------------
    duration_check = None
    if final_video_url:
        print("\nVerifying final video duration (ffprobe)...")
        duration_check = verify_video_duration(final_video_url, total_duration, tolerance=2)
        if duration_check.get("verdict") == "PASS":
            print(f"  ✅ Duration OK: {duration_check['actual_duration']}s "
                  f"(expected {total_duration}s)")
        elif duration_check.get("verdict") == "FAIL":
            print(f"  ❌ DURATION MISMATCH: actual {duration_check['actual_duration']}s "
                  f"vs expected {total_duration}s")
        elif duration_check.get("verdict") == "SKIP":
            print(f"  ⚠️  Duration verification skipped: {duration_check.get('warning')}")

    return {
        "success": True,
        "status": "COMPLETED",
        "endpoint": "/api/video/image-to-video + /api/video/extend-video",
        "model": model,
        "total_duration": total_duration,
        "segment_duration": segment_duration,
        "segment_count": len(segments),
        "video_url": final_video_url,
        "segments": segments,
        "merge": merge_result,
        "delivery": delivery,
        "duration_verification": duration_check,
        "progress_file": progress_file,
    }


def long_reference_mode_video(prompts: list[str],
                              image_urls: list[str] | None = None,
                              video_urls: list[str] | None = None,
                              audio_urls: list[str] | None = None,
                              total_duration: int = 30,
                              segment_duration: int = 10,
                              ratio: str = "9:16",
                              size: str = "720p",
                              model: str = DEFAULT_VIDEO_MODEL,
                              sound: str = "on",
                              force_long: bool = False,
                              progress_file: str | None = None) -> dict:
    """通过 reference-mode-video + extend-video 生成长参考素材视频。

    适用于需要上传商品图、人物图、风格图作为参考的剧情视频。首段通过 imageUrls
    传参考素材，后续段使用上一段视频 URL 调 extend-video。

    时长安全：
    - 默认安全上限是 30 秒。40 秒以上视频必须在用户明确确认分段计划和交付策略后，
      才通过 force_long=True 放行。
    - 每次 extend-video 都会检查累计时长，避免 60 秒请求因为多延展一次变成 70 秒以上。
    - 最终累计结果会在可用时用 ffprobe 校验。
    - 如果设置 progress_file，每步都会保存 segment URL，方便崩溃后恢复。
    """

    if not prompts:
        return {"error": True, "message": "At least one segment prompt is required"}

    if total_duration <= 0 or segment_duration <= 0:
        return {"error": True, "message": "Durations must be positive integers"}

    validation_error = validate_ratio(ratio)
    if validation_error:
        return validation_error

    if model == "seedance-2.0" and segment_duration > SEEDANCE_MAX_SEGMENT_DURATION:
        return {
            "error": True,
            "message": f"segment-duration must be <= {SEEDANCE_MAX_SEGMENT_DURATION}s for seedance-2.0",
            "requested_segment_duration": segment_duration,
        }

    if total_duration > SAFE_MAX_LONG_VIDEO_DURATION:
        if not force_long:
            return {
                "error": True,
                "message": (
                    f"Refusing automatic long-reference-mode-video above "
                    f"{SAFE_MAX_LONG_VIDEO_DURATION}s (requested {total_duration}s). "
                    "The current Borgrise extend/merge workflow is only verified "
                    "for 20s/30s deliverables. Pass --allow-long after the user "
                    "has confirmed a backend-verified concat plan with explicit "
                    "segment count and final delivery + ffprobe verification."
                ),
                "requested_total_duration": total_duration,
                "safe_max_duration": SAFE_MAX_LONG_VIDEO_DURATION,
            }
        print(
            f"⚠️  WARNING: Generating {total_duration}s video with force_long=True. "
            f"Safe max is {SAFE_MAX_LONG_VIDEO_DURATION}s. "
            "Final ffprobe verification will be performed."
        )

    total_segments = -(-total_duration // segment_duration)

    if len(prompts) != total_segments:
        return {
            "error": True,
            "message": (
                "Prompt segment count must exactly match the planned segment count. "
                "Do not add or drop scenes silently."
            ),
            "expected_prompt_count": total_segments,
            "actual_prompt_count": len(prompts),
            "total_duration": total_duration,
            "segment_duration": segment_duration,
        }

    print(f"\n{'='*60}")
    print("LONG REFERENCE MODE VIDEO WORKFLOW")
    print(f"{'='*60}")
    print(f"Target Duration: {total_duration}s")
    print(f"Segment Duration: {segment_duration}s")
    print(f"Segments: {total_segments}")
    print(f"Images: {len(image_urls or [])}")
    print(f"{'='*60}\n")

    segments = []

    first_duration = min(segment_duration, total_duration)
    first_result = reference_mode_video(
        prompt=prompts[0],
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
        duration=first_duration,
        ratio=ratio,
        size=size,
        model=model,
        sound=sound,
        video_count=1,
        auto_poll=True
    )
    if first_result.get("error"):
        return first_result

    current_video_url = first_result.get("video_url")
    segments.append({
        "segment": 1,
        "task_id": first_result.get("task_id"),
        "video_url": current_video_url,
        "endpoint": first_result.get("endpoint"),
        "cumulative_duration": first_duration,
    })

    # ---- 首段完成后保存进度 -------------------------------------------
    if progress_file:
        try:
            _progress = {
                "total_duration": total_duration,
                "segment_duration": segment_duration,
                "segments_completed": 1,
                "total_segments": total_segments,
                "prompts": prompts,
                "segments": segments,
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            os.makedirs(os.path.dirname(progress_file) or ".", exist_ok=True)
            with open(progress_file, "w") as pf:
                json.dump(_progress, pf, indent=2, ensure_ascii=False)
            print(f"Progress saved: 1/{total_segments} segments → {progress_file}")
        except Exception:
            pass

    elapsed = first_duration
    for idx in range(2, total_segments + 1):
        remaining = total_duration - elapsed
        if remaining <= 0:
            break

        seg_dur = min(segment_duration, remaining)
        prompt_index = min(idx - 1, len(prompts) - 1)
        extend_prompt = prompts[prompt_index]

        print(f"\nExtending reference-mode segment {idx}/{total_segments}...")

        extend_result = extend_video(
            video_url=current_video_url,
            duration=seg_dur,
            model=model,
            prompt=extend_prompt,
            ratio=ratio,
            size=size,
            sound=sound,
            auto_poll=True,
            max_total_duration=total_duration,
            current_cumulative_duration=elapsed,
        )
        if extend_result.get("error"):
            return {
                "error": True,
                "message": f"Failed on segment {idx}",
                "segments_completed": len(segments),
                "details": extend_result,
                "segments": segments
            }

        current_video_url = extend_result.get("video_url")
        elapsed += seg_dur
        segments.append({
            "segment": idx,
            "task_id": extend_result.get("task_id"),
            "video_url": current_video_url,
            "endpoint": extend_result.get("endpoint"),
            "cumulative_duration": elapsed,
        })

        # ---- 每段完成后保存进度 ----------------------------------------
        if progress_file:
            try:
                _progress = {
                    "total_duration": total_duration,
                    "segment_duration": segment_duration,
                    "segments_completed": len(segments),
                    "total_segments": total_segments,
                    "prompts": prompts,
                    "segments": segments,
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                os.makedirs(os.path.dirname(progress_file) or ".", exist_ok=True)
                with open(progress_file, "w") as pf:
                    json.dump(_progress, pf, indent=2, ensure_ascii=False)
                print(f"Progress saved: {len(segments)}/{total_segments} segments → {progress_file}")
            except Exception:
                pass  # 进度文件是 best-effort，保存失败不能中断生成流程。

    delivery = select_long_video_delivery(segments)
    if delivery.get("error"):
        return delivery

    final_video_url = delivery.get("video_url")
    merge_result = None

    # ---- 最终时长校验 ---------------------------------------------------
    duration_check = None
    if final_video_url:
        print("\nVerifying final video duration (ffprobe)...")
        duration_check = verify_video_duration(
            final_video_url, total_duration, tolerance=2
        )
        if duration_check.get("verdict") == "PASS":
            print(
                f"  ✅ Duration OK: {duration_check['actual_duration']}s "
                f"(expected {total_duration}s, diff {duration_check['difference']}s)"
            )
        elif duration_check.get("verdict") == "FAIL":
            print(
                f"  ❌ DURATION MISMATCH: actual {duration_check['actual_duration']}s "
                f"vs expected {total_duration}s "
                f"(diff {duration_check['difference']}s) — "
                "the output video may be too long or too short"
            )
        elif duration_check.get("verdict") == "SKIP":
            print(f"  ⚠️  Duration verification skipped: {duration_check.get('warning')}")

    return {
        "success": True,
        "status": "COMPLETED",
        "endpoint": "/api/video/reference-mode-video + /api/video/extend-video",
        "model": model,
        "total_duration": total_duration,
        "segment_duration": segment_duration,
        "segment_count": len(segments),
        "video_url": final_video_url,
        "segments": segments,
        "merge": merge_result,
        "delivery": delivery,
        "duration_verification": duration_check,
        "progress_file": progress_file,
    }


def long_native_audio_reference_video(prompts: list[str],
                                      image_urls: list[str] | None = None,
                                      video_urls: list[str] | None = None,
                                      audio_urls: list[str] | None = None,
                                      total_duration: int = 30,
                                      segment_duration: int = 10,
                                      ratio: str = "9:16",
                                      size: str = "720p",
                                      model: str = DEFAULT_VIDEO_MODEL,
                                      sound: str = "on",
                                      force_long: bool = False,
                                      progress_file: str | None = None,
                                      long_reference_video_fn=long_reference_mode_video) -> dict:
    """生成带模型原生中文音频的长参考视频。"""
    native_prompts = [build_native_audio_prompt(prompt) for prompt in prompts]
    return long_reference_video_fn(
        prompts=native_prompts,
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
        total_duration=total_duration,
        segment_duration=segment_duration,
        ratio=ratio,
        size=size,
        model=model,
        sound=sound,
        force_long=force_long,
        progress_file=progress_file,
    )


def resume_long_reference_mode_video(progress_file: str,
                                     prompts_file: str | None = None,
                                     ratio: str = "9:16",
                                     size: str = "720p",
                                     model: str = DEFAULT_VIDEO_MODEL,
                                     sound: str = "on",
                                     extend_fn=None,
                                     merge_fn=None,
                                     verify_fn=None) -> dict:
    """恢复中断的 long-reference-mode-video 工作流。

    这是 ``long_reference_mode_video`` 进度文件的正式崩溃恢复路径。它会保留段数
    校验、累计时长保护、进度文件更新、最终交付和时长校验，而不是依赖临时脚本。
    """
    extend_fn = extend_fn or extend_video
    verify_fn = verify_fn or verify_video_duration

    validation_error = validate_ratio(ratio)
    if validation_error:
        return validation_error

    if not os.path.isabs(progress_file):
        return {
            "error": True,
            "message": "--progress-file must be an absolute path for reliable resume",
            "progress_file": progress_file,
        }

    try:
        with open(progress_file) as pf:
            progress = json.load(pf)
    except Exception as exc:
        return {
            "error": True,
            "message": f"Could not read progress file: {exc}",
            "progress_file": progress_file,
        }

    total_duration = int(progress.get("total_duration", 0))
    segment_duration = int(progress.get("segment_duration", 0))
    total_segments = int(progress.get("total_segments") or (-(-total_duration // segment_duration)))
    segments = progress.get("segments") or []
    prompts = progress.get("prompts")

    if prompts is None and prompts_file:
        try:
            with open(prompts_file) as pf:
                prompts = json.load(pf)
        except Exception as exc:
            return {
                "error": True,
                "message": f"Could not read prompts file: {exc}",
                "prompts_file": prompts_file,
            }

    if prompts is None:
        return {
            "error": True,
            "message": "Progress file does not contain prompts; pass --prompts-file with the original segment prompts.",
            "progress_file": progress_file,
        }

    if total_duration <= 0 or segment_duration <= 0:
        return {"error": True, "message": "Progress file has invalid durations"}

    if model == "seedance-2.0" and segment_duration > SEEDANCE_MAX_SEGMENT_DURATION:
        return {
            "error": True,
            "message": f"segment-duration must be <= {SEEDANCE_MAX_SEGMENT_DURATION}s for seedance-2.0",
            "requested_segment_duration": segment_duration,
        }

    if len(prompts) != total_segments:
        return {
            "error": True,
            "message": "Prompt segment count must exactly match progress total_segments.",
            "expected_prompt_count": total_segments,
            "actual_prompt_count": len(prompts),
            "total_duration": total_duration,
            "segment_duration": segment_duration,
        }

    if not segments:
        return {
            "error": True,
            "message": "Progress file has no completed segments; rerun long-reference-mode-video instead.",
        }

    segments_completed = int(progress.get("segments_completed") or len(segments))
    if segments_completed != len(segments):
        return {
            "error": True,
            "message": "Progress file mismatch: segments_completed does not match segments length.",
            "segments_completed": segments_completed,
            "segments_length": len(segments),
        }

    if segments_completed > total_segments:
        return {
            "error": True,
            "message": "Progress file already exceeds planned segment count.",
            "segments_completed": segments_completed,
            "total_segments": total_segments,
        }

    elapsed = int(segments[-1].get("cumulative_duration") or segments_completed * segment_duration)
    current_video_url = segments[-1].get("video_url")
    if not current_video_url:
        return {
            "error": True,
            "message": "Last completed segment is missing video_url.",
        }

    print(f"\n{'='*60}")
    print("RESUME LONG REFERENCE MODE VIDEO WORKFLOW")
    print(f"{'='*60}")
    print(f"Progress: {segments_completed}/{total_segments} segments")
    print(f"Target Duration: {total_duration}s")
    print(f"Current Duration: {elapsed}s")
    print(f"{'='*60}\n")

    for idx in range(segments_completed + 1, total_segments + 1):
        remaining = total_duration - elapsed
        if remaining <= 0:
            break

        seg_dur = min(segment_duration, remaining)
        extend_prompt = prompts[idx - 1]

        print(f"\nResuming segment {idx}/{total_segments}...")
        extend_result = extend_fn(
            video_url=current_video_url,
            duration=seg_dur,
            model=model,
            prompt=extend_prompt,
            ratio=ratio,
            size=size,
            sound=sound,
            auto_poll=True,
            max_total_duration=total_duration,
            current_cumulative_duration=elapsed,
        )
        if extend_result.get("error"):
            return {
                "error": True,
                "message": f"Failed while resuming segment {idx}",
                "segments_completed": len(segments),
                "details": extend_result,
                "segments": segments,
            }

        current_video_url = extend_result.get("video_url")
        elapsed += seg_dur
        segments.append({
            "segment": idx,
            "task_id": extend_result.get("task_id"),
            "video_url": current_video_url,
            "endpoint": extend_result.get("endpoint"),
            "cumulative_duration": elapsed,
        })

        progress.update({
            "segments_completed": len(segments),
            "total_segments": total_segments,
            "segments": segments,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        with open(progress_file, "w") as pf:
            json.dump(progress, pf, indent=2, ensure_ascii=False)
        print(f"Progress saved: {len(segments)}/{total_segments} segments → {progress_file}")

    if len(segments) != total_segments or elapsed < total_duration:
        return {
            "error": True,
            "message": "Resume stopped before all planned segments completed.",
            "segments_completed": len(segments),
            "total_segments": total_segments,
            "cumulative_duration": elapsed,
            "total_duration": total_duration,
            "segments": segments,
        }

    delivery = select_long_video_delivery(segments)
    if delivery.get("error"):
        return delivery

    final_video_url = delivery.get("video_url")
    merge_result = None

    duration_check = None
    if final_video_url:
        duration_check = verify_fn(final_video_url, total_duration, tolerance=2)

    return {
        "success": True,
        "status": "COMPLETED",
        "endpoint": "/api/video/reference-mode-video + /api/video/extend-video",
        "model": model,
        "total_duration": total_duration,
        "segment_duration": segment_duration,
        "segment_count": len(segments),
        "video_url": final_video_url,
        "segments": segments,
        "merge": merge_result,
        "delivery": delivery,
        "duration_verification": duration_check,
        "progress_file": progress_file,
    }


def text_to_image(prompt: str | None = None, ratio: str = "1:1",
                  size: str | None = None, model: str = DEFAULT_IMAGE_MODEL,
                  product_description: str | None = None,
                  scene: str = "studio", num_images: int = 1) -> dict:
    """根据文本 prompt 生成图片。"""

    validation_error = validate_ratio(ratio)
    if validation_error:
        return validation_error
    quality = normalize_image_quality(size or default_image_quality_for_model(model))
    quality_error = validate_image_model_quality(model, quality)
    if quality_error:
        return quality_error
    count_error = validate_positive_count(num_images, "num_images")
    if count_error:
        return count_error
    width, height = ratio_to_dimensions(ratio)

    if not prompt and product_description:
        prompt = craft_image_prompt(product_description, scene)
    elif not prompt:
        prompt = "Professional product photography, clean and elegant"

    request_data = {
        "prompt": prompt,
        "negative_prompt": "blurry, low quality, watermark, text overlay",
        "model": model,
        "model_version": model,
        "width": width,
        "height": height,
        "imageSize": quality,
        "num": num_images,
        "seed": None
    }

    print(f"\n{'='*60}")
    print("POST /api/picture/text_to_image")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Ratio: {ratio}")
    print(f"Quality: {quality}")
    print(f"Images: {num_images}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=1, size=quality)
    result = make_request("/picture/text_to_image", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=IMAGE_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    final_data = poll_result.get("data", poll_result)
    raw_image_url = (final_data.get("result", {}).get("url")
                     or final_data.get("result", {}).get("image_url")
                     or final_data.get("url"))
    # 如果 API 的 image_url 返回数组，主图字段取第一张。
    if isinstance(raw_image_url, list) and raw_image_url:
        image_url = raw_image_url[0]
    else:
        image_url = raw_image_url
    image_urls = extract_result_urls(final_data)

    # ---- 图片数量校验 ---------------------------------------------------
    count_warning = None
    if len(image_urls) < num_images:
        count_warning = (
            f"Requested {num_images} images but only {len(image_urls)} URLs "
            "were found in the API response. The remaining images may have "
            "been dropped or are accessible through a different response field. "
            "Check raw_response for full details."
        )
        print(f"  ⚠️  {count_warning}")

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/picture/text_to_image",
        "model": model,
        "requested_images": num_images,
        "returned_images": len(image_urls),
        "image_url": image_url,
        "image_urls": image_urls,
        "count_warning": count_warning,
        "raw_response": poll_result
    }


def ratio_to_dimensions(ratio: str) -> tuple[int, int]:
    """把助手接受的比例字符串转换为 Borgrise width/height 字段。"""
    ratio_map = {
        "1:1": (1, 1),
        "16:9": (16, 9),
        "9:16": (9, 16),
    }
    if ratio not in ratio_map:
        raise ValueError(f"Unsupported ratio for reference image generation: {ratio}. Use 1:1, 16:9, or 9:16.")
    return ratio_map[ratio]


def reference_image(reference_images: list[str], prompt: str, ratio: str = "1:1",
                    size: str = "4K", model: str = DEFAULT_IMAGE_MODEL,
                    strength: float | None = None, max_images: int = 1) -> dict:
    """根据一张或多张参考图生成图片。"""

    if not reference_images:
        return {"error": True, "message": "At least one reference image URL is required"}
    count_error = validate_positive_count(max_images, "max_images")
    if count_error:
        return count_error
    quality_error = validate_image_model_quality(model, size)
    if quality_error:
        return quality_error

    try:
        width, height = ratio_to_dimensions(ratio)
    except ValueError as exc:
        return {"error": True, "message": str(exc)}
    quality = normalize_image_quality(size)

    request_data: dict[str, Any] = {
        "prompt": prompt,
        "reference_image_urls": reference_images,
        "model": model,
        "width": width,
        "height": height,
        "imageSize": quality,
        "max_images": max_images,
        "num": max_images,
    }
    if strength is not None:
        request_data["strength"] = strength

    print(f"\n{'='*60}")
    print("POST /api/picture/multi_reference_image_generation")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"References: {len(reference_images)}")
    print(f"Ratio: {ratio}")
    print(f"Quality: {quality}")
    print(f"Images: {max_images}")
    print(f"{'='*60}\n")

    header_duration = 1 if model in {"gpt-image-2", "nanobanana-pro"} else max_images
    headers = get_headers(model=model, bill_type=2, duration=header_duration, size=quality)
    result = make_request("/picture/multi_reference_image_generation", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=IMAGE_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    final_data = poll_result.get("data", poll_result)
    raw_image_url = (final_data.get("result", {}).get("url")
                     or final_data.get("result", {}).get("image_url")
                     or final_data.get("url"))
    # 如果 API 的 image_url 返回数组，主图字段取第一张。
    if isinstance(raw_image_url, list) and raw_image_url:
        image_url = raw_image_url[0]
    else:
        image_url = raw_image_url
    image_urls = extract_result_urls(final_data)

    # ---- 图片数量校验 ---------------------------------------------------
    count_warning = None
    if len(image_urls) < max_images:
        count_warning = (
            f"Requested {max_images} images but only {len(image_urls)} URLs "
            "were found in the API response. Check raw_response for full details."
        )
        print(f"  ⚠️  {count_warning}")

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/picture/multi_reference_image_generation",
        "model": model,
        "requested_images": max_images,
        "returned_images": len(image_urls),
        "image_url": image_url,
        "image_urls": image_urls,
        "count_warning": count_warning,
        "raw_response": poll_result
    }


def image_edit(
    image_url: str,
    prompt: str,
    model: str = DEFAULT_IMAGE_MODEL,
    ratio: str | None = None,
    size: str | None = None,
    max_images: int = 1,
) -> dict:
    """编辑一张已有图片。"""
    if not image_url:
        return {"error": True, "message": "Image URL is required"}
    count_error = validate_positive_count(max_images, "max_images")
    if count_error:
        return count_error
    quality = normalize_image_quality(size or default_image_quality_for_model(model))
    quality_error = validate_image_model_quality(model, quality)
    if quality_error:
        return quality_error
    ratio_value = ratio or "1:1"
    try:
        width, height = ratio_to_dimensions(ratio_value)
    except ValueError as exc:
        return {"error": True, "message": str(exc)}

    request_data = {
        "image_url": image_url,
        "prompt": prompt,
        "model": model,
        "width": width,
        "height": height,
        "imageSize": quality,
        "size": ratio_value,
        "max_images": max_images,
        "num": max_images,
    }

    print(f"\n{'='*60}")
    print("POST /api/picture/image_edit")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Quality: {quality}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=1, size=quality)
    result = make_request("/picture/image_edit", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=IMAGE_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    final_data = poll_result.get("data", poll_result)
    raw_edited_url = (
        final_data.get("result", {}).get("url")
        or final_data.get("result", {}).get("image_url")
        or final_data.get("url")
        or final_data.get("image_url")
    )
    edited_url = raw_edited_url[0] if isinstance(raw_edited_url, list) and raw_edited_url else raw_edited_url
    image_urls = extract_result_urls(final_data)

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/picture/image_edit",
        "model": model,
        "edited_image_url": edited_url,
        "image_url": edited_url,
        "image_urls": image_urls,
        "raw_response": poll_result
    }


def multi_image_fusion(
    image_urls: list[str],
    prompt: str = "",
    ratio: str = "1:1",
    size: str | None = None,
    model: str = DEFAULT_IMAGE_MODEL,
    num_images: int = 1,
) -> dict:
    """把多张图片融合成一张或多张新图。"""
    image_urls = [url for url in image_urls if url]
    if len(image_urls) < 2:
        return {"error": True, "message": "At least two image URLs are required for multi-image fusion"}
    count_error = validate_positive_count(num_images, "num_images")
    if count_error:
        return count_error
    quality = normalize_image_quality(size or default_image_quality_for_model(model))
    quality_error = validate_image_model_quality(model, quality)
    if quality_error:
        return quality_error
    try:
        width, height = ratio_to_dimensions(ratio)
    except ValueError as exc:
        return {"error": True, "message": str(exc)}

    request_data = {
        "image_urls": image_urls,
        "prompt": prompt,
        "width": width,
        "height": height,
        "model": model,
        "num": num_images,
    }

    print(f"\n{'='*60}")
    print("POST /api/picture/multi_image_fusion")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Images: {len(image_urls)}")
    print(f"Ratio: {ratio}")
    print(f"Quality: {quality}")
    print(f"Outputs: {num_images}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=num_images, size=quality)
    result = make_request("/picture/multi_image_fusion", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id, default_timeout=IMAGE_POLL_TIMEOUT)

    if poll_result.get("error"):
        return poll_result

    final_data = poll_result.get("data", poll_result)
    raw_image_url = (
        final_data.get("result", {}).get("url")
        or final_data.get("result", {}).get("image_url")
        or final_data.get("url")
    )
    image_url = raw_image_url[0] if isinstance(raw_image_url, list) and raw_image_url else raw_image_url
    image_urls_result = extract_result_urls(final_data)

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/picture/multi_image_fusion",
        "model": model,
        "requested_images": num_images,
        "returned_images": len(image_urls_result),
        "image_url": image_url,
        "image_urls": image_urls_result,
        "raw_response": poll_result,
    }


def batch_text_to_image(prompts: list[str], ratio: str = "1:1",
                        size: str | None = None,
                        model: str = DEFAULT_IMAGE_MODEL) -> dict:
    """根据多条 prompt 批量生成图片。"""

    validation_error = validate_ratio(ratio)
    if validation_error:
        return validation_error
    quality = normalize_image_quality(size or default_image_quality_for_model(model))
    quality_error = validate_image_model_quality(model, quality)
    if quality_error:
        return quality_error
    width, height = ratio_to_dimensions(ratio)

    request_data = []
    for p in prompts:
        request_data.append({
            "prompt": p,
            "negative_prompt": "blurry, low quality, watermark, text overlay",
            "model": model,
            "model_version": model,
            "width": width,
            "height": height,
            "imageSize": quality,
            "num": 1
        })

    print(f"\n{'='*60}")
    print("POST /api/picture/batch_text_to_image")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Count: {len(prompts)} images")
    print(f"Ratio: {ratio}")
    print(f"Quality: {quality}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=1, size=quality)
    result = make_request("/picture/batch_text_to_image", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    # 批量端点可能返回多个 task ID。
    task_ids = result.get("data", result).get("taskIds", [])
    if not task_ids:
        # 兼容只返回单个 task ID 的响应。
        single_id = result.get("data", result).get("taskId")
        if single_id:
            task_ids = [single_id]
        else:
            return {"error": True, "message": "No taskIds in response", "response": result}

    print(f"Tasks created: {task_ids}")
    print("Polling for results...\n")

    # 逐个轮询所有任务。
    results = []
    for task_id in task_ids:
        print(f"Polling task {task_id}...")
        poll_result = poll_task(task_id, default_timeout=IMAGE_POLL_TIMEOUT)
        if not poll_result.get("error"):
            final_data = poll_result.get("data", poll_result)
            img_url = final_data.get("result", {}).get("url") or final_data.get("url")
            results.append({
                "task_id": task_id,
                "status": "COMPLETED",
                "image_url": img_url
            })
        else:
            results.append({
                "task_id": task_id,
                "error": True,
                "message": poll_result.get("message")
            })

    return {
        "success": True,
        "endpoint": "/api/picture/batch_text_to_image",
        "model": model,
        "count": len(prompts),
        "results": results
    }


def main():
    parser = argparse.ArgumentParser(
        description="Borgrise AI Content Creation Platform Execution Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # 图片生成视频命令。
    p_i2v = subparsers.add_parser("image-to-video", help="Generate video from image")
    p_i2v.add_argument("--image-url", required=True, help="Product image URL")
    p_i2v.add_argument("--prompt", help="Video generation prompt")
    p_i2v.add_argument("--product-description", help="Product description (will craft prompt)")
    p_i2v.add_argument("--duration", type=int, default=10, help="Video duration in seconds")
    p_i2v.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_i2v.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_i2v.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 文本生成视频命令。
    p_t2v = subparsers.add_parser("text-to-video", help="Generate video from a text-only prompt")
    p_t2v.add_argument("--prompt", required=True, help="Video generation prompt")
    p_t2v.add_argument("--duration", type=int, default=10, help="Video duration in seconds")
    p_t2v.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_t2v.add_argument("--size", default="720p", help="Video size (720p, 1080p)")
    p_t2v.add_argument("--sound", default="on", help="Sound setting, usually on/off")
    p_t2v.add_argument("--video-count", type=int, default=1, help="Number of videos to generate")
    p_t2v.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_t2v.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 参考素材生成视频命令。
    p_ref_video = subparsers.add_parser("reference-mode-video", help="Generate video from reference images/audio/videos")
    p_ref_video.add_argument("--prompt", required=True, help="Video generation prompt")
    p_ref_video.add_argument("--image-urls", default="[]", help="JSON array of reference image URLs")
    p_ref_video.add_argument("--video-urls", default="[]", help="JSON array of reference video URLs")
    p_ref_video.add_argument("--audio-urls", default="[]", help="JSON array of reference audio URLs")
    p_ref_video.add_argument("--duration", type=int, default=10, help="Video duration in seconds")
    p_ref_video.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_ref_video.add_argument("--size", default="720p", help="Video size (720p, 1080p)")
    p_ref_video.add_argument("--sound", default="on", help="Sound setting, usually on/off")
    p_ref_video.add_argument("--video-count", type=int, default=1, help="Number of videos to generate")
    p_ref_video.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_ref_video.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 带模型原生音频的参考素材生成视频命令。
    p_native_ref_video = subparsers.add_parser(
        "native-audio-reference-video",
        help="Generate reference-mode video with model-native Chinese speech/music"
    )
    p_native_ref_video.add_argument("--prompt", required=True, help="Video generation prompt with natural dialogue/voiceover lines")
    p_native_ref_video.add_argument("--image-urls", default="[]", help="JSON array of reference image URLs")
    p_native_ref_video.add_argument("--video-urls", default="[]", help="JSON array of reference video URLs")
    p_native_ref_video.add_argument("--audio-urls", default="[]", help="JSON array of optional reference audio URLs")
    p_native_ref_video.add_argument("--duration", type=int, default=10, help="Video duration in seconds")
    p_native_ref_video.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_native_ref_video.add_argument("--size", default="720p", help="Video size (720p, 1080p)")
    p_native_ref_video.add_argument("--sound", default="on", help="Sound setting, keep on for native audio")
    p_native_ref_video.add_argument("--video-count", type=int, default=1, help="Number of videos to generate")
    p_native_ref_video.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_native_ref_video.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 视频延展命令。
    p_extend = subparsers.add_parser("extend-video", help="Extend an existing video")
    p_extend.add_argument("--video-url", required=True, help="Existing video URL")
    p_extend.add_argument("--prompt", help="Extension prompt (must contain @filename reference)")
    p_extend.add_argument("--duration", type=int, default=10, help="Extension duration in seconds")
    p_extend.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_extend.add_argument("--size", default="720p", help="Video size (720p, 1080p)")
    p_extend.add_argument("--sound", default="on", help="Sound setting, usually on/off")
    p_extend.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_extend.add_argument("--max-total-duration", type=int, help="Refuse extend if cumulative duration would exceed this")
    p_extend.add_argument("--current-cumulative", type=int, default=0, help="Total duration already generated so far")
    p_extend.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 图片生成长视频命令。
    p_long_i2v = subparsers.add_parser("long-image-to-video", help="Generate a long video by image-to-video + repeated extend-video")
    p_long_i2v.add_argument("--image-url", required=True, help="Product image URL")
    p_long_i2v.add_argument("--prompt", help="Video generation prompt")
    p_long_i2v.add_argument("--product-description", help="Product description (will craft prompt)")
    p_long_i2v.add_argument("--total-duration", type=int, default=20, help="Target total duration in seconds")
    p_long_i2v.add_argument("--segment-duration", type=int, default=10, help="Per-segment duration in seconds")
    p_long_i2v.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_long_i2v.add_argument("--size", default="720p", help="Video size (720p, 1080p, 4K)")
    p_long_i2v.add_argument("--sound", default="on", help="Sound setting, usually on/off")
    p_long_i2v.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_long_i2v.add_argument("--allow-long", action="store_true", help="Bypass 30s safe-max limit after user confirmation")
    p_long_i2v.add_argument("--progress-file", help="Save segment progress to this JSON file")
    p_long_i2v.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 参考素材生成长视频命令。
    p_long_ref = subparsers.add_parser("long-reference-mode-video", help="Generate a long video by reference-mode-video + extend-video with final duration verification")
    p_long_ref.add_argument("--prompts", required=True, help="JSON array of segment prompts. First prompt creates the video; later prompts extend it.")
    p_long_ref.add_argument("--image-urls", default="[]", help="JSON array of reference image URLs")
    p_long_ref.add_argument("--video-urls", default="[]", help="JSON array of reference video URLs")
    p_long_ref.add_argument("--audio-urls", default="[]", help="JSON array of reference audio URLs")
    p_long_ref.add_argument("--total-duration", type=int, default=30, help="Target total duration in seconds")
    p_long_ref.add_argument("--segment-duration", type=int, default=10, help="Per-segment duration in seconds")
    p_long_ref.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_long_ref.add_argument("--size", default="720p", help="Video size (720p, 1080p)")
    p_long_ref.add_argument("--allow-long", action="store_true", help="Bypass 30s safe-max limit after user confirmation")
    p_long_ref.add_argument("--progress-file", help="Save segment progress to this JSON file")
    p_long_ref.add_argument("--sound", default="on", help="Sound setting, usually on/off")
    p_long_ref.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_long_ref.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 带模型原生音频的参考素材长视频命令。
    p_long_native_ref = subparsers.add_parser(
        "long-native-audio-reference-video",
        help="Generate a long reference video with model-native Chinese speech/music"
    )
    p_long_native_ref.add_argument("--prompts", required=True, help="JSON array of segment prompts with natural dialogue/voiceover lines.")
    p_long_native_ref.add_argument("--image-urls", default="[]", help="JSON array of reference image URLs")
    p_long_native_ref.add_argument("--video-urls", default="[]", help="JSON array of reference video URLs")
    p_long_native_ref.add_argument("--audio-urls", default="[]", help="JSON array of optional reference audio URLs")
    p_long_native_ref.add_argument("--total-duration", type=int, default=30, help="Target total duration in seconds")
    p_long_native_ref.add_argument("--segment-duration", type=int, default=10, help="Per-segment duration in seconds")
    p_long_native_ref.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_long_native_ref.add_argument("--size", default="720p", help="Video size (720p, 1080p)")
    p_long_native_ref.add_argument("--allow-long", action="store_true", help="Bypass 30s safe-max limit after user confirmation")
    p_long_native_ref.add_argument("--progress-file", help="Save segment progress to this JSON file")
    p_long_native_ref.add_argument("--sound", default="on", help="Sound setting, keep on for native audio")
    p_long_native_ref.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_long_native_ref.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 长参考视频恢复命令。
    p_resume_long_ref = subparsers.add_parser(
        "resume-long-reference-mode-video",
        help="Resume an interrupted long-reference-mode-video workflow from a progress file"
    )
    p_resume_long_ref.add_argument("--progress-file", required=True, help="Absolute progress JSON path from the original run")
    p_resume_long_ref.add_argument("--prompts-file", help="JSON file containing the original segment prompts if progress lacks prompts")
    p_resume_long_ref.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_resume_long_ref.add_argument("--size", default="720p", help="Video size (720p, 1080p, 4K)")
    p_resume_long_ref.add_argument("--sound", default="on", help="Sound setting, usually on/off")
    p_resume_long_ref.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_resume_long_ref.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # 文本生成图片命令。
    p_t2i = subparsers.add_parser("text-to-image", help="Generate image from text")
    p_t2i.add_argument("--prompt", help="Image generation prompt")
    p_t2i.add_argument("--product-description", help="Product description (will craft prompt)")
    p_t2i.add_argument("--scene", default="studio", help="Scene type (studio/lifestyle/flatlay/hero)")
    p_t2i.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_t2i.add_argument("--size", default=None, help="Image quality (1080p, 2K, 3K, 4K, etc.); omitted uses model default")
    p_t2i.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")
    p_t2i.add_argument("--num-images", type=int, default=1, help="Number of images to generate")

    # 参考图生成图片命令。
    p_ref = subparsers.add_parser("reference-image", help="Generate image from one or more reference images")
    p_ref.add_argument("--reference-images", required=True, help="JSON array of reference image URLs")
    p_ref.add_argument("--prompt", required=True, help="Image generation prompt")
    p_ref.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_ref.add_argument("--size", default="4K", help="Image quality/size, e.g. 4K")
    p_ref.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")
    p_ref.add_argument("--strength", type=float, help="Reference strength, if supported by the model")
    p_ref.add_argument("--max-images", type=int, default=1, help="Number of images to generate")

    # 图片编辑命令。
    p_edit = subparsers.add_parser("image-edit", help="Edit an existing image")
    p_edit.add_argument("--image-url", required=True, help="Original image URL")
    p_edit.add_argument("--prompt", required=True, help="Edit instruction")
    p_edit.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")
    p_edit.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_edit.add_argument("--size", default=None, help="Image quality/size, e.g. 1080p, 2K, 4K")
    p_edit.add_argument("--max-images", type=int, default=1, help="Number of edited images to generate")

    # 批量文本生成图片命令。
    p_batch = subparsers.add_parser("batch-text-to-image", help="Batch generate images")
    p_batch.add_argument("--prompts", required=True, help="JSON array of prompts")
    p_batch.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_batch.add_argument("--size", default=None, help="Image quality (1080p, 2K, 3K, 4K, etc.); omitted uses model default")
    p_batch.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")

    # 任务轮询命令。
    p_poll = subparsers.add_parser("poll", help="Poll task status")
    p_poll.add_argument("--task-id", required=True, help="Task ID to poll")
    p_poll.add_argument("--poll-timeout", type=int, help="Override this command's poll timeout in seconds")

    # upload-file
    p_upload = subparsers.add_parser("upload-file", help="Upload a local file and return its Borgrise URL")
    p_upload.add_argument("--file-path", required=True, help="Local file path to upload")

    # create-virtual-human-asset
    p_virtual_asset = subparsers.add_parser(
        "create-virtual-human-asset",
        help="Create a Borgrise virtual human asset and print an asset:// reference"
    )
    p_virtual_asset.add_argument("--asset-name", required=True, help="Asset display name")
    p_virtual_asset.add_argument("--image-url", help="Public image URL for the portrait")
    p_virtual_asset.add_argument("--image-file", help="Local portrait image file to upload first")
    p_virtual_asset.add_argument("--description", default="", help="Asset description")
    p_virtual_asset.add_argument("--sex", default="female", help="Asset sex metadata, female/male")
    p_virtual_asset.add_argument("--age", default="20", help="Asset age metadata")
    p_virtual_asset.add_argument("--price", type=float, default=0.5, help="Asset price metadata")
    p_virtual_asset.add_argument("--visibility", type=int, default=0, help="0 private, 1 public")

    # resolve-assets
    p_resolve_assets = subparsers.add_parser("resolve-assets", help="Resolve Borgrise asset ids to URLs")
    p_resolve_assets.add_argument("--asset-ids", required=True, help="JSON array of asset ids or asset:// refs")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 命令行模式不再支持配置静态 token/账号密码。真实扣费调用必须从 FastAPI
    # 请求进入，由网关把 content-app Authorization 写入 ContextVar 后再执行。

    # Apply --poll-timeout override if provided
    poll_timeout_override = getattr(args, "poll_timeout", None)
    if poll_timeout_override is not None:
        global _cli_poll_timeout
        _cli_poll_timeout = poll_timeout_override
        print(f"Poll timeout override: {poll_timeout_override}s")

    # Execute command
    try:
        if args.command == "image-to-video":
            result = image_to_video(
                image_url=args.image_url,
                prompt=args.prompt,
                duration=args.duration,
                ratio=args.ratio,
                model=args.model,
                product_description=args.product_description
            )
        elif args.command == "text-to-video":
            result = text_to_video(
                prompt=args.prompt,
                duration=args.duration,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                sound=args.sound,
                video_count=args.video_count
            )
        elif args.command == "reference-mode-video":
            result = reference_mode_video(
                prompt=args.prompt,
                image_urls=json.loads(args.image_urls),
                video_urls=json.loads(args.video_urls),
                audio_urls=json.loads(args.audio_urls),
                duration=args.duration,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                sound=args.sound,
                video_count=args.video_count
            )
        elif args.command == "native-audio-reference-video":
            result = native_audio_reference_video(
                prompt=args.prompt,
                image_urls=json.loads(args.image_urls),
                video_urls=json.loads(args.video_urls),
                audio_urls=json.loads(args.audio_urls),
                duration=args.duration,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                sound=args.sound,
                video_count=args.video_count
            )
        elif args.command == "extend-video":
            result = extend_video(
                video_url=args.video_url,
                duration=args.duration,
                model=args.model,
                prompt=args.prompt,
                ratio=args.ratio,
                size=args.size,
                sound=getattr(args, "sound", "on"),
                max_total_duration=args.max_total_duration,
                current_cumulative_duration=args.current_cumulative,
            )
        elif args.command == "long-image-to-video":
            result = long_image_to_video(
                image_url=args.image_url,
                prompt=args.prompt,
                total_duration=args.total_duration,
                segment_duration=args.segment_duration,
                ratio=args.ratio,
                size=getattr(args, "size", "720p"),
                model=args.model,
                product_description=args.product_description,
                sound=getattr(args, "sound", "on"),
                force_long=getattr(args, "allow_long", False),
                progress_file=getattr(args, "progress_file", None),
            )
        elif args.command == "long-reference-mode-video":
            result = long_reference_mode_video(
                prompts=json.loads(args.prompts),
                image_urls=json.loads(args.image_urls),
                video_urls=json.loads(args.video_urls),
                audio_urls=json.loads(args.audio_urls),
                total_duration=args.total_duration,
                segment_duration=args.segment_duration,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                sound=args.sound,
                force_long=getattr(args, "allow_long", False),
                progress_file=getattr(args, "progress_file", None),
            )
        elif args.command == "long-native-audio-reference-video":
            result = long_native_audio_reference_video(
                prompts=json.loads(args.prompts),
                image_urls=json.loads(args.image_urls),
                video_urls=json.loads(args.video_urls),
                audio_urls=json.loads(args.audio_urls),
                total_duration=args.total_duration,
                segment_duration=args.segment_duration,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                sound=args.sound,
                force_long=getattr(args, "allow_long", False),
                progress_file=getattr(args, "progress_file", None),
            )
        elif args.command == "resume-long-reference-mode-video":
            result = resume_long_reference_mode_video(
                progress_file=args.progress_file,
                prompts_file=args.prompts_file,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                sound=args.sound,
            )
        elif args.command == "text-to-image":
            result = text_to_image(
                prompt=args.prompt,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                product_description=args.product_description,
                scene=args.scene,
                num_images=args.num_images
            )
        elif args.command == "reference-image":
            reference_images = json.loads(args.reference_images)
            result = reference_image(
                reference_images=reference_images,
                prompt=args.prompt,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                strength=args.strength,
                max_images=args.max_images
            )
        elif args.command == "image-edit":
            result = image_edit(
                image_url=args.image_url,
                prompt=args.prompt,
                model=args.model,
                ratio=args.ratio,
                size=args.size,
                max_images=args.max_images,
            )
        elif args.command == "batch-text-to-image":
            prompts = json.loads(args.prompts)
            result = batch_text_to_image(
                prompts=prompts,
                ratio=args.ratio,
                size=args.size,
                model=args.model
            )
        elif args.command == "poll":
            result = poll_task(args.task_id)
        elif args.command == "upload-file":
            result = upload_file(args.file_path)
        elif args.command == "create-virtual-human-asset":
            result = create_virtual_human_asset(
                asset_name=args.asset_name,
                image_url=args.image_url,
                image_file=args.image_file,
                description=args.description,
                sex=args.sex,
                age=args.age,
                price=args.price,
                visibility=args.visibility
            )
        elif args.command == "resolve-assets":
            result = resolve_asset_urls(json.loads(args.asset_ids))
        else:
            parser.print_help()
            sys.exit(1)

        # Output result
        print("\n" + "="*60)
        print("RESULT")
        print("="*60)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
