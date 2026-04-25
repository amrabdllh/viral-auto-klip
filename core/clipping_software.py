"""
Optional integration for https://api.clipping.software.

The local engine remains the default. Use this only when you have an API key
and want to compare with the hosted clipping API.
"""
import time
from typing import Dict, Optional

import requests


BASE_URL = "https://api.clipping.software"


def submit_clip_job(
    api_key: str,
    output_type: str,
    number_of_clips: int,
    captions: bool,
    caption_style: Optional[Dict] = None,
    video_url: str = "",
    yt_link: str = "",
    ai_clipping: bool = True,
) -> Dict:
    payload = {
        "finalVideo_type": _map_output_type(output_type),
        "numberOfClips": int(number_of_clips),
        "captions": bool(captions),
        "ai_clipping": bool(ai_clipping),
    }
    if caption_style:
        payload["captionStyle"] = {
            "font_size": int(caption_style.get("font_size", 22)),
            "vertical_position": int(caption_style.get("vertical_position", 130)),
            "border_thickness": int(caption_style.get("border_thickness", 2)),
        }
    if yt_link:
        payload["yt_link"] = yt_link
    elif video_url:
        payload["videoUrl"] = video_url
    else:
        raise ValueError("Clipping.software mode requires a public URL or YouTube link.")

    response = requests.post(
        f"{BASE_URL}/generate",
        headers=_headers(api_key),
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def get_clip_status(api_key: str, task_id: str) -> Dict:
    response = requests.get(
        f"{BASE_URL}/status",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"taskID": task_id},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def wait_for_clip_job(api_key: str, task_id: str, poll_seconds: int = 8, timeout_seconds: int = 1800) -> Dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = get_clip_status(api_key, task_id)
        if str(status.get("status", "")).upper() in {"COMPLETED", "DONE", "SUCCESS"}:
            return status
        if str(status.get("status", "")).upper() in {"FAILED", "ERROR"}:
            raise RuntimeError(f"Clipping.software task failed: {status}")
        time.sleep(poll_seconds)
    raise TimeoutError(f"Timed out waiting for Clipping.software task {task_id}")


def _headers(api_key: str) -> Dict:
    if not api_key:
        raise ValueError("Missing Clipping.software API key.")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _map_output_type(output_type: str) -> str:
    if output_type == "Wide":
        return "Wide"
    if output_type == "Fit":
        return "Fit"
    return "Vertical"
