#!/usr/bin/env python3
"""
Borgrise AI Content Creation Platform - Execution Script

Usage:
    python run_generation.py image-to-video --image-url URL --prompt "..." [--duration 5] [--ratio "9:16"]
    python run_generation.py text-to-image --prompt "..." [--ratio "1:1"] [--size "1024x1024"]
    python run_generation.py image-edit --image-url URL --prompt "..."
    python run_generation.py batch-text-to-image --prompts '["prompt1", "prompt2", ...]' [--ratio "1:1"]
    python run_generation.py poll --task-id TASK_ID

Environment Variables:
    BORGRISE_API_TOKEN: Your API bearer token (required)
    BORGRISE_BASE_URL: API base URL (default: https://test-video.borgrise.com/api)
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List

# Configuration
BASE_URL = os.environ.get("BORGRISE_BASE_URL", "https://test-video.borgrise.com/api")
API_TOKEN = os.environ.get("BORGRISE_API_TOKEN", "")

# Default models
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_VIDEO_MODEL = "seedance-2.0"

# Polling settings
POLL_INTERVAL = 5  # seconds
POLL_TIMEOUT = 600  # seconds (10 minutes)


def get_headers(model: str = "", bill_type: int = 0,
                 duration: int = 1, size: str = "720p") -> Dict[str, str]:
    """Get request headers with auth token and required custom headers.

    Custom headers required by Borgrise API:
      - modelType: the model name (e.g. seedance-2.0, gpt-image-2)
      - billType: 2 = image (per-image billing), 3 = video (per-second billing)
      - apiModelParamObj: JSON string with model params (e.g. {"size":"720p"})
      - duration: generation duration (seconds for video, 1 for image)
    """
    if not API_TOKEN:
        raise ValueError("BORGRISE_API_TOKEN environment variable is not set. Please set it before running.")
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    if model:
        headers["modelType"] = model
    if bill_type:
        headers["billType"] = str(bill_type)
    if duration:
        headers["duration"] = str(duration)
    # apiModelParamObj - model parameter configuration
    api_param = {"size": size}
    headers["apiModelParamObj"] = json.dumps(api_param)
    return headers


def make_request(endpoint: str, data: Optional[Dict] = None, method: str = "POST",
                  custom_headers: Optional[Dict[str, str]] = None) -> Dict:
    """Make HTTP request to the API."""
    url = f"{BASE_URL}{endpoint}"
    # Use custom_headers if provided, otherwise get default (polling) headers
    if custom_headers:
        headers = custom_headers
    else:
        headers = {
            "Authorization": f"Bearer {API_TOKEN}",
            "Content-Type": "application/json"
        }

    if data:
        body = json.dumps(data).encode("utf-8")
    else:
        body = None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        return {
            "error": True,
            "status_code": e.code,
            "message": error_body
        }
    except Exception as e:
        return {
            "error": True,
            "message": str(e)
        }


def poll_task(task_id: str, timeout: int = POLL_TIMEOUT) -> Dict:
    """Poll task status until completion or timeout."""
    start_time = time.time()
    last_status = None

    while time.time() - start_time < timeout:
        result = make_request(f"/task/{task_id}/status", method="GET")

        if result.get("error"):
            return result

        status = result.get("data", result).get("status", "UNKNOWN")

        if status != last_status:
            print(f"Task {task_id}: {status}")
            last_status = status

        if status == "COMPLETED":
            return result
        elif status == "FAILED":
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
        "message": f"Polling timeout after {timeout} seconds",
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


def image_to_video(image_url: str, prompt: Optional[str] = None,
                   duration: int = 10, ratio: str = "9:16",
                   model: str = DEFAULT_VIDEO_MODEL,
                   product_description: Optional[str] = None,
                   auto_poll: bool = True) -> Dict:
    """Generate video from image."""

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

    headers = get_headers(model=model, bill_type=3, duration=duration, size="720p")
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


def extend_video(video_url: str, duration: int = 10,
                 model: str = DEFAULT_VIDEO_MODEL,
                 prompt: Optional[str] = None,
                 ratio: str = "9:16",
                 size: str = "720p",
                 auto_poll: bool = True) -> Dict:
    """Extend an existing video using the correct API format.

    The extend-video API requires:
    - refVideoList: array of video URLs
    - prompt: must include "@filename" to reference the video
    - projectId: optional query parameter
    """

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
        "sound": "on",
        "videoCount": "1"
    }

    print(f"\n{'='*60}")
    print(f"POST /api/video/extend-video?projectId=108")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Duration: {duration}s")
    print(f"Size: {size}")
    print(f"Reference: {filename}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=3, duration=duration, size=size)
    result = make_request("/video/extend-video?projectId=108", request_data, custom_headers=headers)

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

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/video/extend-video",
        "model": model,
        "video_url": extended_video_url,
        "raw_response": poll_result
    }


def long_image_to_video(image_url: str, prompt: Optional[str] = None,
                        total_duration: int = 20, segment_duration: int = 10,
                        ratio: str = "9:16", model: str = DEFAULT_VIDEO_MODEL,
                        product_description: Optional[str] = None,
                        size: str = "720p") -> Dict:
    """Generate a long video by creating an initial segment then extending it repeatedly.

    Note: seedance-2.0 supports max 10s per segment, so we use extend-video for longer videos.
    The extend-video API requires the prompt to contain @filename reference.
    """

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
        # Adjust: use segment_duration for all but last segment which may be shorter
        last_segment = total_duration - (total_segments - 1) * segment_duration
        if last_segment <= 0:
            total_segments = total_duration // segment_duration
            last_segment = segment_duration

    print(f"\n{'='*60}")
    print(f"LONG IMAGE TO VIDEO WORKFLOW")
    print(f"{'='*60}")
    print(f"Target Duration: {total_duration}s")
    print(f"Segment Duration: {segment_duration}s")
    print(f"Segments: {total_duration // segment_duration}")
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
        "endpoint": first_result.get("endpoint")
    })

    # Extend for remaining segments
    for idx in range(2, total_segments + 1):
        print(f"\nExtending segment {idx}/{total_segments}...")

        # Last segment may be shorter if total_duration is not a multiple of segment_duration
        seg_dur = last_segment if (idx == total_segments and total_duration % segment_duration != 0) else segment_duration

        # Create extension prompt with content description
        extend_prompt = f"延续之前的视频内容，继续保持画面节奏和风格，动态镜头，流畅过渡"

        extend_result = extend_video(
            video_url=current_video_url,
            duration=seg_dur,
            model=model,
            prompt=extend_prompt,
            ratio=ratio,
            size=size,
            auto_poll=True
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
        segments.append({
            "segment": idx,
            "task_id": extend_result.get("task_id"),
            "video_url": current_video_url,
            "endpoint": extend_result.get("endpoint")
        })

    return {
        "success": True,
        "status": "COMPLETED",
        "endpoint": "/api/video/image-to-video + /api/video/extend-video",
        "model": model,
        "total_duration": total_duration,
        "segment_duration": segment_duration,
        "segment_count": len(segments),
        "video_url": current_video_url,
        "segments": segments
    }


def text_to_image(prompt: Optional[str] = None, ratio: str = "1:1",
                  size: str = "1024x1024", model: str = DEFAULT_IMAGE_MODEL,
                  product_description: Optional[str] = None,
                  scene: str = "studio") -> Dict:
    """Generate image from text."""

    if not prompt and product_description:
        prompt = craft_image_prompt(product_description, scene)
    elif not prompt:
        prompt = "Professional product photography, clean and elegant"

    request_data = {
        "prompt": prompt,
        "negative_prompt": "blurry, low quality, watermark, text overlay",
        "model": model,
        "ratio": ratio,
        "size": size,
        "num_images": 1,
        "seed": None
    }

    print(f"\n{'='*60}")
    print(f"POST /api/picture/text_to_image")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Ratio: {ratio}")
    print(f"Size: {size}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=1, size=size)
    result = make_request("/picture/text_to_image", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = result.get("data", result).get("taskId")
    if not task_id:
        return {"error": True, "message": "No taskId in response", "response": result}

    print(f"Task created: {task_id}")
    print(f"Polling for result...\n")

    poll_result = poll_task(task_id)

    if poll_result.get("error"):
        return poll_result

    final_data = poll_result.get("data", poll_result)
    image_url = final_data.get("result", {}).get("url") or final_data.get("url")

    return {
        "success": True,
        "task_id": task_id,
        "status": "COMPLETED",
        "endpoint": "/api/picture/text_to_image",
        "model": model,
        "image_url": image_url,
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

    headers = get_headers(model=model, bill_type=2, duration=1, size="1024x1024")
    result = make_request("/picture/image_edit", request_data, custom_headers=headers)

    if result.get("error"):
        return result

    task_id = result.get("data", result).get("taskId")
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
                        size: str = "1024x1024",
                        model: str = DEFAULT_IMAGE_MODEL) -> Dict:
    """Batch generate images from multiple prompts."""

    request_data = []
    for p in prompts:
        request_data.append({
            "prompt": p,
            "negative_prompt": "blurry, low quality, watermark, text overlay",
            "model": model,
            "ratio": ratio,
            "size": size,
            "num_images": 1
        })

    print(f"\n{'='*60}")
    print(f"POST /api/picture/batch_text_to_image")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Count: {len(prompts)} images")
    print(f"Ratio: {ratio}")
    print(f"{'='*60}\n")

    headers = get_headers(model=model, bill_type=2, duration=1, size=size)
    result = make_request("/picture/batch_text_to_image", request_data, custom_headers=headers)

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

    # extend-video
    p_extend = subparsers.add_parser("extend-video", help="Extend an existing video")
    p_extend.add_argument("--video-url", required=True, help="Existing video URL")
    p_extend.add_argument("--prompt", help="Extension prompt (must contain @filename reference)")
    p_extend.add_argument("--duration", type=int, default=10, help="Extension duration in seconds")
    p_extend.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_extend.add_argument("--size", default="720p", help="Video size (720p, 1080p)")
    p_extend.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")

    # long-image-to-video
    p_long_i2v = subparsers.add_parser("long-image-to-video", help="Generate a long video by image-to-video + repeated extend-video")
    p_long_i2v.add_argument("--image-url", required=True, help="Product image URL")
    p_long_i2v.add_argument("--prompt", help="Video generation prompt")
    p_long_i2v.add_argument("--product-description", help="Product description (will craft prompt)")
    p_long_i2v.add_argument("--total-duration", type=int, default=20, help="Target total duration in seconds")
    p_long_i2v.add_argument("--segment-duration", type=int, default=10, help="Per-segment duration in seconds")
    p_long_i2v.add_argument("--ratio", default="9:16", help="Aspect ratio")
    p_long_i2v.add_argument("--model", default=DEFAULT_VIDEO_MODEL, help="Model to use")

    # text-to-image
    p_t2i = subparsers.add_parser("text-to-image", help="Generate image from text")
    p_t2i.add_argument("--prompt", help="Image generation prompt")
    p_t2i.add_argument("--product-description", help="Product description (will craft prompt)")
    p_t2i.add_argument("--scene", default="studio", help="Scene type (studio/lifestyle/flatlay/hero)")
    p_t2i.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_t2i.add_argument("--size", default="1024x1024", help="Image size")
    p_t2i.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")

    # image-edit
    p_edit = subparsers.add_parser("image-edit", help="Edit an existing image")
    p_edit.add_argument("--image-url", required=True, help="Original image URL")
    p_edit.add_argument("--prompt", required=True, help="Edit instruction")
    p_edit.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")

    # batch-text-to-image
    p_batch = subparsers.add_parser("batch-text-to-image", help="Batch generate images")
    p_batch.add_argument("--prompts", required=True, help="JSON array of prompts")
    p_batch.add_argument("--ratio", default="1:1", help="Aspect ratio")
    p_batch.add_argument("--size", default="1024x1024", help="Image size")
    p_batch.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help="Model to use")

    # poll
    p_poll = subparsers.add_parser("poll", help="Poll task status")
    p_poll.add_argument("--task-id", required=True, help="Task ID to poll")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Check token
    if not API_TOKEN:
        print("ERROR: BORGRISE_API_TOKEN environment variable is not set.")
        print("\nSet it with:")
        print("  export BORGRISE_API_TOKEN='your-token-here'")
        print("\nOr run:")
        print("  BORGRISE_API_TOKEN='your-token' python run_generation.py ...")
        sys.exit(1)

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
        elif args.command == "extend-video":
            result = extend_video(
                video_url=args.video_url,
                duration=args.duration,
                model=args.model,
                prompt=args.prompt,
                ratio=args.ratio,
                size=args.size
            )
        elif args.command == "long-image-to-video":
            result = long_image_to_video(
                image_url=args.image_url,
                prompt=args.prompt,
                total_duration=args.total_duration,
                segment_duration=args.segment_duration,
                ratio=args.ratio,
                model=args.model,
                product_description=args.product_description
            )
        elif args.command == "text-to-image":
            result = text_to_image(
                prompt=args.prompt,
                ratio=args.ratio,
                size=args.size,
                model=args.model,
                product_description=args.product_description,
                scene=args.scene
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
