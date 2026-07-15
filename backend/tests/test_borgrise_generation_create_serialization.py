from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pixelflow.skills.borgrise import run_generation


def test_content_app_generation_create_requests_are_serialized_but_polls_can_overlap(monkeypatch):
    """PixelFlow may launch many scene/page jobs, but content-app task creation must be one-by-one.

    content-app confirms quota after the create endpoint returns, so concurrent create requests can
    all see the same remaining quota. Polling already happens after task creation and can overlap.
    """

    monkeypatch.setattr(run_generation, "get_headers", lambda *args, **kwargs: {})

    create_endpoints = {
        "/video/text-to-video": "video",
        "/picture/text_to_image": "image",
        "/picture/smart-ppt/generatePptImage": "ppt-image",
    }
    lock = threading.Lock()
    create_active = 0
    poll_active = 0
    max_create_active = 0
    max_poll_active = 0
    task_counter = 0

    def fake_make_request(endpoint, data=None, *args, **kwargs):
        nonlocal create_active, max_create_active, task_counter
        assert endpoint in create_endpoints
        with lock:
            task_counter += 1
            task_id = f"{create_endpoints[endpoint]}-{task_counter}"
            create_active += 1
            max_create_active = max(max_create_active, create_active)
        time.sleep(0.03)
        with lock:
            create_active -= 1
        return {"success": True, "data": {"taskId": task_id, "smartPptProjectId": 88}}

    def fake_poll_task(task_id, timeout=None, *, default_timeout=None):
        nonlocal poll_active, max_poll_active
        with lock:
            poll_active += 1
            max_poll_active = max(max_poll_active, poll_active)
        time.sleep(0.05)
        with lock:
            poll_active -= 1
        if task_id.startswith("video-"):
            result = {"videoUrl": f"https://x/{task_id}.mp4"}
        elif task_id.startswith("image-"):
            result = {"url": f"https://x/{task_id}.png"}
        else:
            result = f"https://x/{task_id}.png"
        return {"success": True, "data": {"status": "completed", "result": result}}

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)
    monkeypatch.setattr(run_generation, "poll_task", fake_poll_task)

    def run_video():
        return run_generation.text_to_video("video scene", duration=5, auto_poll=True)

    def run_image():
        return run_generation.text_to_image("product image", num_images=1)

    def run_ppt_image():
        return run_generation.generate_ppt_image(json_content='{"page_index":1}', smart_ppt_project_id=88)

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = [future.result() for future in [executor.submit(fn) for fn in (run_video, run_image, run_ppt_image)]]

    assert all(result["success"] for result in results)
    assert max_create_active == 1
    assert max_poll_active > 1
