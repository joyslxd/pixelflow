#!/usr/bin/env python3
"""
Borgrise AI Content Creation Platform - Execution Script

Usage:
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

Environment Variables:
    BORGRISE_API_TOKEN: Your API bearer token
    BORGRISE_USERNAME: Borgrise username for automatic token refresh
    BORGRISE_PASSWORD: Borgrise password for automatic token refresh
    BORGRISE_BASE_URL: API base URL (default: https://test-video.borgrise.com/api)
    BORGRISE_PROJECT_ID: Project ID for generation APIs (default: 1)
"""

import os
import sys
import json
import time
import argparse
import ssl
import urllib.request
import urllib.error
import re
from typing import Optional, Dict, Any, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

# Configuration
BASE_URL = os.environ.get("BORGRISE_BASE_URL", "https://test-video.borgrise.com/api")
API_TOKEN = os.environ.get("BORGRISE_API_TOKEN", "")
BORGRISE_USERNAME = os.environ.get("BORGRISE_USERNAME", "")
BORGRISE_PASSWORD = os.environ.get("BORGRISE_PASSWORD", "")
PROJECT_ID = os.environ.get("BORGRISE_PROJECT_ID", "1")
SKIP_SSL_VERIFY = os.environ.get("BORGRISE_SKIP_SSL_VERIFY", "").lower() in {"1", "true", "yes", "on"}
SSL_CONTEXT = ssl._create_unverified_context() if SKIP_SSL_VERIFY else None

# Default models
DEFAULT_IMAGE_MODEL = "seeddream-5.0"
DEFAULT_VIDEO_MODEL = "seedance-2.0"
SUPPORTED_RATIOS = {"1:1", "9:16", "16:9"}
SUPPORTED_IMAGE_QUALITIES = {"all", "480p", "720p", "1080p", "2K", "3K", "4K", "5K", "6K", "7K", "8K"}
SEEDANCE_MAX_SEGMENT_DURATION = 10
SAFE_MAX_LONG_VIDEO_DURATION = 30

# Polling settings
POLL_INTERVAL = 5  # seconds
POLL_TIMEOUT = int(os.environ.get("BORGRISE_POLL_TIMEOUT", "600"))  # seconds (10 min default)
_cli_poll_timeout: Optional[int] = None  # overridden by --poll-timeout CLI flag

# Retry settings
MAX_REQUEST_RETRIES = int(os.environ.get("BORGRISE_MAX_RETRIES", "3"))
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def _effective_poll_timeout() -> int:
    """Return the CLI-overridden poll timeout if set, else the env/default."""
    return _cli_poll_timeout if _cli_poll_timeout is not None else POLL_TIMEOUT


def validate_ratio(ratio: str) -> Optional[Dict]:
    """Reject ratios that the current Borgrise GPT/video workflows do not support."""
    if ratio not in SUPPORTED_RATIOS:
        return {
            "error": True,
            "message": f"Unsupported ratio '{ratio}'. Use one of: 1:1, 9:16, 16:9.",
            "supported_ratios": sorted(SUPPORTED_RATIOS),
        }
    return None


def normalize_image_quality(size: str) -> str:
    """Normalize legacy image size values to the quality labels used by Borgrise pricing config."""
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


def validate_image_quality(size: str) -> Optional[Dict]:
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


def validate_video_duration(duration: int, model: str) -> Optional[Dict]:
    """Keep single video calls within known model limits."""
    if duration <= 0:
        return {"error": True, "message": "Duration must be a positive integer"}
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


def validate_positive_count(count: int, field_name: str) -> Optional[Dict]:
    """Validate requested output counts so user-requested N images is not collapsed."""
    if count <= 0:
        return {"error": True, "message": f"{field_name} must be a positive integer"}
    return None


def extract_result_urls(data: Any) -> List[str]:
    """Best-effort extraction for single or multi-image/video task result URLs."""
    urls: List[str] = []

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
                 model_header: str = "ModelType") -> Dict[str, str]:
    """Get request headers with auth token and required custom headers.

    Custom headers required by Borgrise API:
      - ModelType/modelType: the model name. content-app_ec uses ModelType
        for image endpoints and modelType for video endpoints.
      - billType: 2 = image (per-image billing), 3 = video (per-second billing)
      - apiModelParamObj: JSON string with model params (e.g. {"size":"720p"})
      - duration: generation duration (seconds for video, 1 for image)
    """
    ensure_api_token()
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    if model:
        headers[model_header] = model
    if bill_type:
        headers["billType"] = str(bill_type)
    if duration:
        headers["duration"] = str(duration)
    # apiModelParamObj - model parameter configuration
    api_param = {"size": size}
    headers["apiModelParamObj"] = json.dumps(api_param)
    return headers


def with_project(endpoint: str, project_id: str = PROJECT_ID) -> str:
    """Append projectId like the Borgrise test frontend does for generation APIs."""
    if not project_id or "projectId=" in endpoint:
        return endpoint
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}projectId={project_id}"


def _extract_token(payload: Dict[str, Any]) -> Optional[str]:
    """Extract a login token from known Borgrise response shapes."""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    return (
        data.get("token")
        or data.get("accessToken")
        or data.get("access_token")
        or data.get("jwt")
        or payload.get("token")
        or payload.get("accessToken")
        or payload.get("access_token")
    )


def _looks_token_expired(payload: Dict[str, Any]) -> bool:
    """Detect Borgrise token-expiry responses across HTTP and JSON shapes."""
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


def login_and_refresh_token() -> str:
    """Login with BORGRISE_USERNAME/PASSWORD and refresh the process token."""
    global API_TOKEN

    if not BORGRISE_USERNAME or not BORGRISE_PASSWORD:
        raise ValueError(
            "BORGRISE_API_TOKEN is expired and BORGRISE_USERNAME/BORGRISE_PASSWORD "
            "are not configured for automatic refresh."
        )

    login_url = f"{BASE_URL}/auth/login"
    body = json.dumps({
        "username": BORGRISE_USERNAME,
        "password": BORGRISE_PASSWORD,
    }).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        raise RuntimeError(f"Borgrise login failed: HTTP {e.code} {error_body}") from e

    token = _extract_token(payload)
    if not token:
        raise RuntimeError(f"Borgrise login succeeded but no token was found: {payload}")

    API_TOKEN = token
    os.environ["BORGRISE_API_TOKEN"] = token
    print("Borgrise API token refreshed automatically.")
    return token


def ensure_api_token() -> str:
    """Return a usable token, logging in first when only credentials exist."""
    if API_TOKEN:
        return API_TOKEN
    return login_and_refresh_token()


def _apply_auth_header(headers: Dict[str, str]) -> Dict[str, str]:
    updated = dict(headers)
    updated["Authorization"] = f"Bearer {ensure_api_token()}"
    return updated


def _send_request(url: str, body: Optional[bytes], headers: Dict[str, str], method: str) -> Dict:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        return {
            "error": True,
            "status_code": e.code,
            "message": error_body
        }


def make_request(endpoint: str, data: Optional[Dict] = None, method: str = "POST",
                  custom_headers: Optional[Dict[str, str]] = None,
                  _retry_on_token_expired: bool = True) -> Dict:
    """Make HTTP request to the API with retry on transient errors.

    Retries on:
      - HTTP 429 (rate limit) and 5xx (server errors)
      - Network-level errors (URLError, TimeoutError, OSError)

    Uses exponential backoff: 2s, 4s, 8s... between retries.
    Set BORGRISE_MAX_RETRIES env var to override the default (3).
    """
    url = f"{BASE_URL}{endpoint}"
    # Use custom_headers if provided, otherwise get default (polling) headers
    if custom_headers:
        headers = _apply_auth_header(custom_headers)
    else:
        headers = _apply_auth_header({
            "Content-Type": "application/json"
        })

    if data:
        body = json.dumps(data).encode("utf-8")
    else:
        body = None

    last_error: Optional[Dict] = None
    for attempt in range(MAX_REQUEST_RETRIES):
        try:
            result = _send_request(url, body, headers, method)
            if _retry_on_token_expired and _looks_token_expired(result):
                login_and_refresh_token()
                headers = _apply_auth_header(headers)
                return make_request(
                    endpoint,
                    data=data,
                    method=method,
                    custom_headers=headers,
                    _retry_on_token_expired=False,
                )
            if not result.get("error"):
                return result

            status_code = result.get("status_code")
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

    # All retries exhausted
    return last_error or {"error": True, "message": "All retries exhausted"}


def make_multipart_request(endpoint: str, file_field: str, file_path: str,
                           fields: Optional[Dict[str, str]] = None) -> Dict:
    """Upload a local file using multipart/form-data."""
    if not os.path.exists(file_path):
        return {"error": True, "message": f"File does not exist: {file_path}"}

    boundary = f"----BorgriseBoundary{int(time.time() * 1000)}"
    body = bytearray()

    for key, value in (fields or {}).items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    filename = os.path.basename(file_path)
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8")
    )
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    with open(file_path, "rb") as file_obj:
        body.extend(file_obj.read())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    headers = _apply_auth_header(headers)

    last_error: Optional[Dict] = None
    for attempt in range(MAX_REQUEST_RETRIES):
        try:
            req = urllib.request.Request(
                f"{BASE_URL}{endpoint}",
                data=bytes(body),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
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


def poll_task(task_id: str, timeout: Optional[int] = None) -> Dict:
    """Poll task status until completion or timeout.

    Args:
        task_id: The Borgrise task ID to poll.
        timeout: Max seconds to wait. Falls back to --poll-timeout CLI flag,
                 then BORGRISE_POLL_TIMEOUT env var, then 600s default.
    """
    effective_timeout = timeout if timeout is not None else _effective_poll_timeout()
    start_time = time.time()
    last_status = None

    while time.time() - start_time < effective_timeout:
        result = make_request(f"/task/{task_id}/status", method="GET")

        if result.get("error"):
            return result

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
        "last_status": last_status
    }


def craft_video_prompt(product_description: str, style: str = "cinematic") -> str:
    """Craft a detailed video prompt from product description."""
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
    """Craft a detailed image prompt from product description."""
    scene_styles = {
        "studio": "on a clean white surface, soft studio lighting from above, professional product photography",
        "lifestyle": "in an elegant lifestyle setting, natural window light, aspirational aesthetic",
        "flatlay": "flat lay composition, overhead view, clean arrangement, Instagram-worthy",
        "hero": "hero shot, front view, dramatic lighting, premium showcase"
    }

    scene_desc = scene_styles.get(scene, scene_styles["studio"])
    return f"{product_description}, {scene_desc}, high resolution, no watermark"


def extract_task_id(result: Dict) -> Optional[str]:
    """Extract task ID from API response supporting multiple key styles."""
    data = result.get("data", result)
    return data.get("taskId") or data.get("task_id") or result.get("task_id") or result.get("taskId")


def extract_video_url(result: Dict) -> Optional[str]:
    """Extract video URL from polling/API response supporting multiple layouts."""
    final_data = result.get("data", result)
    return (
        final_data.get("result", {}).get("video_url")
        or final_data.get("result", {}).get("url")
        or final_data.get("video_url")
        or final_data.get("url")
    )


def verify_video_duration(video_url: str, expected_duration: int,
                          tolerance: int = 2) -> Dict:
    """Verify actual video duration using ffprobe (best-effort).

    Returns a dict with:
      - verified: bool — whether ffprobe was available and ran
      - actual_duration: float — the measured duration in seconds
      - within_tolerance: bool — whether the actual duration is within tolerance
      - verdict: "PASS" | "FAIL" | "SKIP"
      - warning: str — present when ffprobe is unavailable or failed

    This is best-effort: if ffprobe is not installed or the URL is
    unreachable, it returns a warning rather than an error. Callers
    should treat a FAIL verdict as a generation defect.
    """
    import subprocess
    import shutil

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


def extract_uploaded_url(result: Dict) -> Optional[str]:
    """Extract an uploaded file URL from Borgrise upload response variants."""
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


def extract_asset_id(result: Dict) -> Optional[str]:
    """Extract a third-party digital-human asset id from response variants."""
    data = result.get("data", result)
    return (
        data.get("assetId")
        or data.get("asset_id")
        or data.get("thirdAssetId")
        or data.get("third_asset_id")
        or result.get("assetId")
        or result.get("thirdAssetId")
    )


def upload_file(file_path: str) -> Dict:
    """Upload a local file to Borgrise and return its public URL."""
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
                               image_url: Optional[str] = None,
                               image_file: Optional[str] = None,
                               description: str = "",
                               sex: str = "female",
                               age: str = "20",
                               price: float = 0.5,
                               visibility: int = 0,
                               project_id: str = PROJECT_ID) -> Dict:
    """Create a virtual human asset and return an asset:// reference."""
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
        "projectId": int(project_id),
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


def resolve_asset_urls(asset_ids: List[str]) -> Dict:
    """Resolve Borgrise asset IDs to reference URLs via the frontend endpoint."""
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


def image_to_video(image_url: str, prompt: Optional[str] = None,
                   duration: int = 10, ratio: str = "9:16",
                   model: str = DEFAULT_VIDEO_MODEL,
                   product_description: Optional[str] = None,
                   auto_poll: bool = True) -> Dict:
    """Generate video from image."""

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
        "seed": None
    }

    print(f"\n{'='*60}")
    print(f"POST /api/video/image-to-video")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Ratio: {ratio}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size="720p", model_header="modelType")
    result = make_request(with_project("/video/image-to-video"), request_data, custom_headers=headers)

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
    print(f"Polling for result...\n")

    poll_result = poll_task(task_id)

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


def text_to_video(prompt: str,
                  duration: int = 10,
                  ratio: str = "9:16",
                  size: str = "720p",
                  model: str = DEFAULT_VIDEO_MODEL,
                  sound: str = "on",
                  video_count: int = 1,
                  auto_poll: bool = True) -> Dict:
    """Generate video from a text-only prompt."""

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
    result = make_request(with_project("/video/text-to-video"), request_data, custom_headers=headers)

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

    poll_result = poll_task(task_id)

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
                         image_urls: Optional[List[str]] = None,
                         video_urls: Optional[List[str]] = None,
                         audio_urls: Optional[List[str]] = None,
                         duration: int = 10,
                         ratio: str = "9:16",
                         size: str = "720p",
                         model: str = DEFAULT_VIDEO_MODEL,
                         sound: str = "on",
                         video_count: int = 1,
                         auto_poll: bool = True) -> Dict:
    """Generate video from multimodal reference materials.

    This mirrors the Borgrise test frontend's "reference mode" call. Use it
    when uploaded images/audio/videos are references rather than a single first
    frame.
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
    result = make_request(with_project("/video/reference-mode-video"), request_data, custom_headers=headers)

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

    poll_result = poll_task(task_id)

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


def _strip_prompt_timecode(prompt: str) -> str:
    return re.sub(r"^\s*\d+\s*-\s*\d+\s*s[:：]\s*", "", prompt).strip()


def _extract_primary_dialogue(prompt: str) -> Optional[str]:
    cleaned = _strip_prompt_timecode(prompt)
    markers = ["女播主说：", "女播主说:", "旁白说：", "旁白说:", "女1说：", "女1说:"]
    for marker in markers:
        if marker in cleaned:
            dialogue = cleaned.split(marker, 1)[1].strip()
            return dialogue.strip(" \"'") if dialogue else None
    return None


def build_native_audio_prompt(prompt: str) -> str:
    """Wrap a user video prompt with a speech-first contract.

    Root-cause testing showed that front-loading long technical instructions,
    timecodes, and shot grammar can pollute the first few seconds of generated
    speech. Keep the spoken line first and keep the non-spoken constraints short.
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
                                 image_urls: Optional[List[str]] = None,
                                 video_urls: Optional[List[str]] = None,
                                 audio_urls: Optional[List[str]] = None,
                                 duration: int = 10,
                                 ratio: str = "9:16",
                                 size: str = "720p",
                                 model: str = DEFAULT_VIDEO_MODEL,
                                 sound: str = "on",
                                 video_count: int = 1,
                                 reference_video_fn=reference_mode_video) -> Dict:
    """Generate reference-mode video with model-native Chinese audio."""
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
                 prompt: Optional[str] = None,
                 ratio: str = "9:16",
                 size: str = "720p",
                 sound: str = "on",
                 auto_poll: bool = True,
                 max_total_duration: Optional[int] = None,
                 current_cumulative_duration: int = 0,
                 project_id: str = PROJECT_ID) -> Dict:
    """Extend an existing video using the correct API format.

    The extend-video API requires:
    - refVideoList: array of video URLs
    - prompt: must include "@filename" to reference the video
    - projectId: optional query parameter

    Duration safety (Critical):
    - max_total_duration: when set, the function will refuse to extend
      if current_cumulative_duration + duration would exceed it.
    - current_cumulative_duration: the total duration already generated
      across previous segments. Pass 0 for the first extend after a
      10s first segment, 10 for the second extend, etc.
    - Example: for a 30s target, call extend 1 with (cum=10, max=30),
      extend 2 with (cum=20, max=30). A third extend (cum=30, max=30)
      would be rejected because 30+10 > 30.
    """

    # ---- cumulative-duration guard ------------------------------------
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

    # Extract filename from URL for @filename reference
    filename = video_url.split("/")[-1]
    if not prompt:
        prompt = f"将@{filename}向后延伸，延长内容为延续之前的视频内容"

    # Ensure prompt contains @filename reference
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
    print(f"POST /api/video/extend-video?projectId={project_id}")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Size: {size}")
    print(f"Sound: {sound}")
    print(f"Reference: {filename}")
    print(f"Prompt preview: {prompt[:180]}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request(with_project("/video/extend-video", project_id=project_id), request_data, custom_headers=headers)

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
    print(f"Polling for result...\n")

    poll_result = poll_task(task_id)

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


def merge_videos(video_urls: List[str],
                 project_id: str = "108",
                 model: str = DEFAULT_VIDEO_MODEL,
                 duration: int = 30,
                 size: str = "1080p") -> Dict:
    """Merge video pieces into a single deliverable.

    Swagger defines VideoMergeRequest as projectId + videoUrls. The older
    snake_case video_urls field is rejected by the backend.
    """

    if len(video_urls) < 2:
        return {"error": True, "message": "At least two video URLs are required for merge"}

    request_data = {
        "projectId": int(project_id),
        "videoUrls": video_urls
    }

    print(f"\n{'='*60}")
    print("POST /api/video/merge")
    print(f"{'='*60}")
    print(f"Videos: {len(video_urls)}")
    print(f"Project ID: {project_id}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size, model_header="modelType")
    result = make_request("/video/merge", request_data, custom_headers=headers)

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


def select_long_video_delivery(segments: List[Dict]) -> Dict:
    """Select the final deliverable for extend-based long video workflows.

    Borgrise extend-video returns cumulative videos: each extend result already
    includes the previous content plus the new continuation. Therefore the last
    segment URL is the complete deliverable. Re-merging the first segment with
    the last cumulative result duplicates the opening segment and makes 60s
    videos become roughly 70s.
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


def long_image_to_video(image_url: str, prompt: Optional[str] = None,
                        total_duration: int = 20, segment_duration: int = 10,
                        ratio: str = "9:16", model: str = DEFAULT_VIDEO_MODEL,
                        product_description: Optional[str] = None,
                        size: str = "720p",
                        sound: str = "on",
                        force_long: bool = False,
                        progress_file: Optional[str] = None) -> Dict:
    """Generate a long video by creating an initial segment then extending it repeatedly.

    Note: seedance-2.0 supports max 10s per segment, so we use extend-video for longer
    videos. Cumulative duration tracking prevents over-generation. The final
    cumulative extend result is verified with ffprobe.
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

    # Calculate segments: first segment = segment_duration, remaining via extend
    total_segments = -(-total_duration // segment_duration)  # ceiling division
    actual_total = total_segments * segment_duration
    if actual_total != total_duration:
        last_segment = total_duration - (total_segments - 1) * segment_duration
        if last_segment <= 0:
            total_segments = total_duration // segment_duration
            last_segment = segment_duration
    else:
        last_segment = segment_duration

    print(f"\n{'='*60}")
    print(f"LONG IMAGE TO VIDEO WORKFLOW")
    print(f"{'='*60}")
    print(f"Target Duration: {total_duration}s")
    print(f"Segment Duration: {segment_duration}s")
    print(f"Segments: {total_segments}")
    print(f"{'='*60}\n")

    segments = []

    # Generate first segment
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

    # ---- save progress after first segment --------------------------
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

    elapsed = segment_duration

    # Extend for remaining segments
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

        # ---- save progress after each segment --------------------------
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
                pass

    delivery = select_long_video_delivery(segments)
    if delivery.get("error"):
        return delivery

    final_video_url = delivery.get("video_url")
    merge_result = None

    # ---- final duration verification ------------------------------------
    duration_check = None
    if final_video_url:
        print(f"\nVerifying final video duration (ffprobe)...")
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


def long_reference_mode_video(prompts: List[str],
                              image_urls: Optional[List[str]] = None,
                              video_urls: Optional[List[str]] = None,
                              audio_urls: Optional[List[str]] = None,
                              total_duration: int = 30,
                              segment_duration: int = 10,
                              ratio: str = "9:16",
                              size: str = "720p",
                              model: str = DEFAULT_VIDEO_MODEL,
                              sound: str = "on",
                              force_long: bool = False,
                              progress_file: Optional[str] = None) -> Dict:
    """Generate a long reference-material video by reference-mode-video + extend-video.

    Use this for story videos that need uploaded product/person/style images as
    references. The first segment sends images through imageUrls; continuation
    segments use extend-video with the previous video URL.

    Duration safety:
    - Default safe max is 30s. For 40s+ videos, pass force_long=True after the
      user has explicitly confirmed the segment plan and merge strategy.
    - Cumulative duration tracking is enforced on every extend-video call so an
      accidental extra extend cannot produce a 70s+ video for a 60s request.
    - The final cumulative extend result is verified with ffprobe when available.
    - If progress_file is set, segment URLs are saved after each step so a
      crashed run can be resumed.
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

    # ---- save progress after first segment --------------------------
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
            project_id=PROJECT_ID,
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

        # ---- save progress after each segment --------------------------
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
                pass  # progress file is best-effort, never fatal

    delivery = select_long_video_delivery(segments)
    if delivery.get("error"):
        return delivery

    final_video_url = delivery.get("video_url")
    merge_result = None

    # ---- final duration verification ------------------------------------
    duration_check = None
    if final_video_url:
        print(f"\nVerifying final video duration (ffprobe)...")
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


def long_native_audio_reference_video(prompts: List[str],
                                      image_urls: Optional[List[str]] = None,
                                      video_urls: Optional[List[str]] = None,
                                      audio_urls: Optional[List[str]] = None,
                                      total_duration: int = 30,
                                      segment_duration: int = 10,
                                      ratio: str = "9:16",
                                      size: str = "720p",
                                      model: str = DEFAULT_VIDEO_MODEL,
                                      sound: str = "on",
                                      force_long: bool = False,
                                      progress_file: Optional[str] = None,
                                      long_reference_video_fn=long_reference_mode_video) -> Dict:
    """Generate a long reference video with model-native Chinese audio."""
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
                                     prompts_file: Optional[str] = None,
                                     ratio: str = "9:16",
                                     size: str = "720p",
                                     model: str = DEFAULT_VIDEO_MODEL,
                                     sound: str = "on",
                                     extend_fn=None,
                                     merge_fn=None,
                                     verify_fn=None) -> Dict:
    """Resume an interrupted long-reference-mode-video workflow.

    This is the official crash-recovery path for progress files produced by
    long_reference_mode_video. It preserves segment-count validation,
    cumulative-duration guards, progress-file updates, final delivery, and
    duration verification instead of relying on ad hoc resume scripts.
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
            project_id=PROJECT_ID,
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


def text_to_image(prompt: Optional[str] = None, ratio: str = "1:1",
                  size: str = "1080p", model: str = DEFAULT_IMAGE_MODEL,
                  product_description: Optional[str] = None,
                  scene: str = "studio", num_images: int = 1) -> Dict:
    """Generate image from text."""

    validation_error = validate_ratio(ratio)
    if validation_error:
        return validation_error
    quality_error = validate_image_quality(size)
    if quality_error:
        return quality_error
    count_error = validate_positive_count(num_images, "num_images")
    if count_error:
        return count_error
    width, height = ratio_to_dimensions(ratio)
    quality = normalize_image_quality(size)

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
    print(f"POST /api/picture/text_to_image")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Ratio: {ratio}")
    print(f"Quality: {quality}")
    print(f"Images: {num_images}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=1, size=quality)
    result = make_request(with_project("/picture/text_to_image"), request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    print(f"Task created: {task_id}")
    print(f"Polling for result...\n")

    poll_result = poll_task(task_id)

    if poll_result.get("error"):
        return poll_result

    final_data = poll_result.get("data", poll_result)
    raw_image_url = (final_data.get("result", {}).get("url")
                     or final_data.get("result", {}).get("image_url")
                     or final_data.get("url"))
    # If the API returned an array for image_url, take the first element
    if isinstance(raw_image_url, list) and raw_image_url:
        image_url = raw_image_url[0]
    else:
        image_url = raw_image_url
    image_urls = extract_result_urls(final_data)

    # ---- image-count verification ------------------------------------
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
    """Convert ratio strings accepted by the assistant to Borgrise width/height fields."""
    ratio_map = {
        "1:1": (1, 1),
        "16:9": (16, 9),
        "9:16": (9, 16),
    }
    if ratio not in ratio_map:
        raise ValueError(f"Unsupported ratio for reference image generation: {ratio}. Use 1:1, 16:9, or 9:16.")
    return ratio_map[ratio]


def reference_image(reference_images: List[str], prompt: str, ratio: str = "1:1",
                    size: str = "4K", model: str = DEFAULT_IMAGE_MODEL,
                    strength: Optional[float] = None, max_images: int = 1) -> Dict:
    """Generate an image from one or more reference images."""

    if not reference_images:
        return {"error": True, "message": "At least one reference image URL is required"}
    count_error = validate_positive_count(max_images, "max_images")
    if count_error:
        return count_error
    quality_error = validate_image_quality(size)
    if quality_error:
        return quality_error

    try:
        width, height = ratio_to_dimensions(ratio)
    except ValueError as exc:
        return {"error": True, "message": str(exc)}
    quality = normalize_image_quality(size)

    request_data: Dict[str, Any] = {
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
    result = make_request(with_project("/picture/multi_reference_image_generation"), request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    print(f"Task created: {task_id}")
    print("Polling for result...\n")

    poll_result = poll_task(task_id)

    if poll_result.get("error"):
        return poll_result

    final_data = poll_result.get("data", poll_result)
    raw_image_url = (final_data.get("result", {}).get("url")
                     or final_data.get("result", {}).get("image_url")
                     or final_data.get("url"))
    # If the API returned an array for image_url, take the first element
    if isinstance(raw_image_url, list) and raw_image_url:
        image_url = raw_image_url[0]
    else:
        image_url = raw_image_url
    image_urls = extract_result_urls(final_data)

    # ---- image-count verification ------------------------------------
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


def image_edit(image_url: str, prompt: str, model: str = DEFAULT_IMAGE_MODEL) -> Dict:
    """Edit an existing image."""

    request_data = {
        "image_url": image_url,
        "prompt": prompt,
        "negative_prompt": "blurry, low quality, distorted product",
        "model": model,
        "mask": None
    }

    print(f"\n{'='*60}")
    print(f"POST /api/picture/image_edit")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=1, size="1080p")
    result = make_request(with_project("/picture/image_edit"), request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = extract_task_id(result)
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    print(f"Task created: {task_id}")
    print(f"Polling for result...\n")

    poll_result = poll_task(task_id)

    if poll_result.get("error"):
        return poll_result

    final_data = poll_result.get("data", poll_result)
    edited_url = final_data.get("result", {}).get("url") or final_data.get("url")

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/picture/image_edit",
        "model": model,
        "edited_image_url": edited_url,
        "raw_response": poll_result
    }


def batch_text_to_image(prompts: List[str], ratio: str = "1:1",
                        size: str = "1080p",
                        model: str = DEFAULT_IMAGE_MODEL) -> Dict:
    """Batch generate images from multiple prompts."""

    validation_error = validate_ratio(ratio)
    if validation_error:
        return validation_error
    quality_error = validate_image_quality(size)
    if quality_error:
        return quality_error
    width, height = ratio_to_dimensions(ratio)
    quality = normalize_image_quality(size)

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
    print(f"POST /api/picture/batch_text_to_image")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Count: {len(prompts)} images")
    print(f"Ratio: {ratio}")
    print(f"Quality: {quality}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=1, size=quality)
    result = make_request(with_project("/picture/batch_text_to_image"), request_data, custom_headers=headers)

    if result.get("error"):
        return result

    # Batch endpoint may return multiple task IDs
    task_ids = result.get("data", result).get("taskIds", [])
    if not task_ids:
        # Single task ID case
        single_id = result.get("data", result).get("taskId")
        if single_id:
            task_ids = [single_id]
        else:
            return {"error": True, "message": "No taskIds in response", "response": result}

    print(f"Tasks created: {task_ids}")
    print(f"Polling for results...\n")

    # Poll all tasks
    results = []
    for task_id in task_ids:
        print(f"Polling task {task_id}...")
        poll_result = poll_task(task_id)
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

    # image-to-video
    p_i2v = subparsers.add_parser("image-to-video", help="Generate video from image")
    p_i2v.add_argument("--image-url", required=True, help="Product image URL")
    p_i2v.add_argument("--prompt", help="Video generation prompt")
    p_i2v.add_argument("--product-description", help="Product description (will craft prompt)")
    p_i2v.add_argument("--duration", type=int, default=10, help="Video duration in seconds")
    p_i2v.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_i2v.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_i2v.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # text-to-video
    p_t2v = subparsers.add_parser("text-to-video", help="Generate video from a text-only prompt")
    p_t2v.add_argument("--prompt", required=True, help="Video generation prompt")
    p_t2v.add_argument("--duration", type=int, default=10, help="Video duration in seconds")
    p_t2v.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_t2v.add_argument("--size", default="720p", help="Video size (720p, 1080p)")
    p_t2v.add_argument("--sound", default="on", help="Sound setting, usually on/off")
    p_t2v.add_argument("--video-count", type=int, default=1, help="Number of videos to generate")
    p_t2v.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")
    p_t2v.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # reference-mode-video
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
    p_ref_video.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # native-audio-reference-video
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
    p_native_ref_video.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # extend-video
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
    p_extend.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # long-image-to-video
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
    p_long_i2v.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # long-reference-mode-video
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
    p_long_ref.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # long-native-audio-reference-video
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
    p_long_native_ref.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # resume-long-reference-mode-video
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
    p_resume_long_ref.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

    # text-to-image
    p_t2i = subparsers.add_parser("text-to-image", help="Generate image from text")
    p_t2i.add_argument("--prompt", help="Image generation prompt")
    p_t2i.add_argument("--product-description", help="Product description (will craft prompt)")
    p_t2i.add_argument("--scene", default="studio", help="Scene type (studio/lifestyle/flatlay/hero)")
    p_t2i.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_t2i.add_argument("--size", default="1080p", help="Image quality (1080p, 2K, 4K, etc.)")
    p_t2i.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")
    p_t2i.add_argument("--num-images", type=int, default=1, help="Number of images to generate")

    # reference-image
    p_ref = subparsers.add_parser("reference-image", help="Generate image from one or more reference images")
    p_ref.add_argument("--reference-images", required=True, help="JSON array of reference image URLs")
    p_ref.add_argument("--prompt", required=True, help="Image generation prompt")
    p_ref.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_ref.add_argument("--size", default="4K", help="Image quality/size, e.g. 4K")
    p_ref.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")
    p_ref.add_argument("--strength", type=float, help="Reference strength, if supported by the model")
    p_ref.add_argument("--max-images", type=int, default=1, help="Number of images to generate")

    # image-edit
    p_edit = subparsers.add_parser("image-edit", help="Edit an existing image")
    p_edit.add_argument("--image-url", required=True, help="Original image URL")
    p_edit.add_argument("--prompt", required=True, help="Edit instruction")
    p_edit.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")

    # batch-text-to-image
    p_batch = subparsers.add_parser("batch-text-to-image", help="Batch generate images")
    p_batch.add_argument("--prompts", required=True, help="JSON array of prompts")
    p_batch.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_batch.add_argument("--size", default="1080p", help="Image quality (1080p, 2K, 4K, etc.)")
    p_batch.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")

    # poll
    p_poll = subparsers.add_parser("poll", help="Poll task status")
    p_poll.add_argument("--task-id", required=True, help="Task ID to poll")
    p_poll.add_argument("--poll-timeout", type=int, help="Override poll timeout in seconds (env: BORGRISE_POLL_TIMEOUT)")

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
    p_virtual_asset.add_argument("--project-id", default=PROJECT_ID, help="Borgrise project id")

    # resolve-assets
    p_resolve_assets = subparsers.add_parser("resolve-assets", help="Resolve Borgrise asset ids to URLs")
    p_resolve_assets.add_argument("--asset-ids", required=True, help="JSON array of asset ids or asset:// refs")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Check token or login credentials
    if not API_TOKEN and not (BORGRISE_USERNAME and BORGRISE_PASSWORD):
        print("ERROR: BORGRISE_API_TOKEN is not set and automatic login credentials are missing.")
        print("\nSet either:")
        print("  export BORGRISE_API_TOKEN='your-token-here'")
        print("\nOr:")
        print("  export BORGRISE_USERNAME='your-username'")
        print("  export BORGRISE_PASSWORD='your-password'")
        sys.exit(1)

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
                model=args.model
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
                visibility=args.visibility,
                project_id=args.project_id
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
