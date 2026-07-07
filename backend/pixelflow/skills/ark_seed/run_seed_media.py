#!/usr/bin/env python3
"""CLI for Ark Seedance and Seedream atomic generation skills."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from pixelflow.skills.ark_seed import SeedanceSkill, SeedreamSkill


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(v, str) for v in parsed):
        raise argparse.ArgumentTypeError("expected a JSON string list")
    return parsed


def _print_result(result: Any) -> None:
    if hasattr(result, "__dict__"):
        payload = result.__dict__
    else:
        payload = result
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _run(args: argparse.Namespace) -> None:
    if args.command == "text-to-video":
        result = await SeedanceSkill().text_to_video(
            args.prompt,
            duration=args.duration,
            ratio=args.ratio,
            model=args.model,
            resolution=args.resolution,
            generate_audio=args.generate_audio,
            watermark=args.watermark,
        )
    elif args.command == "image-to-video":
        result = await SeedanceSkill().image_to_video(
            args.image_url,
            prompt=args.prompt,
            duration=args.duration,
            ratio=args.ratio,
            model=args.model,
            generate_audio=args.generate_audio,
            watermark=args.watermark,
        )
    elif args.command == "reference-to-video":
        result = await SeedanceSkill().reference_to_video(
            prompt=args.prompt,
            image_urls=args.image_urls,
            video_urls=args.video_urls,
            audio_urls=args.audio_urls,
            duration=args.duration,
            ratio=args.ratio,
            model=args.model,
            resolution=args.resolution,
            generate_audio=args.generate_audio,
            watermark=args.watermark,
        )
    elif args.command == "poll-video":
        result = await SeedanceSkill().poll_video_task(args.task_id)
    elif args.command == "text-to-image":
        result = await SeedreamSkill().text_to_image(
            args.prompt,
            size=args.size,
            ratio=args.ratio,
            num_images=args.num_images,
            model=args.model,
            sequential_image_generation=args.sequential_image_generation,
            stream=args.stream,
            watermark=not args.no_watermark,
        )
    elif args.command == "image-to-image":
        result = await SeedreamSkill().image_to_image(
            args.image_urls,
            args.prompt,
            size=args.size,
            ratio=args.ratio,
            num_images=args.num_images,
            model=args.model,
            sequential_image_generation=args.sequential_image_generation,
            stream=args.stream,
            watermark=not args.no_watermark,
        )
    elif args.command == "reference-group-images":
        result = await SeedreamSkill().reference_group_images(
            args.image_urls,
            args.prompt,
            size=args.size,
            ratio=args.ratio,
            max_images=args.max_images,
            model=args.model,
            stream=args.stream,
            watermark=not args.no_watermark,
        )
    else:
        raise SystemExit(f"unknown command: {args.command}")
    _print_result(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_video_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--prompt", required=True)
        p.add_argument("--duration", type=int, default=5)
        p.add_argument("--ratio", default="9:16")
        p.add_argument("--resolution", default=None)
        p.add_argument("--model", default=None)
        p.add_argument("--generate-audio", action="store_true")
        p.add_argument("--watermark", action="store_true")

    p = sub.add_parser("text-to-video")
    add_video_common(p)

    p = sub.add_parser("image-to-video")
    add_video_common(p)
    p.add_argument("--image-url", required=True)

    p = sub.add_parser("reference-to-video")
    add_video_common(p)
    p.add_argument("--image-urls", type=_json_list, default=[])
    p.add_argument("--video-urls", type=_json_list, default=[])
    p.add_argument("--audio-urls", type=_json_list, default=[])

    p = sub.add_parser("poll-video")
    p.add_argument("--task-id", required=True)

    def add_image_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--prompt", required=True)
        p.add_argument("--size", default="2K")
        p.add_argument("--ratio", default="1:1")
        p.add_argument("--model", default=None)
        p.add_argument("--sequential-image-generation", default="disabled")
        p.add_argument("--stream", action="store_true")
        p.add_argument("--no-watermark", action="store_true")

    p = sub.add_parser("text-to-image")
    add_image_common(p)
    p.add_argument("--num-images", type=int, default=1)

    p = sub.add_parser("image-to-image")
    add_image_common(p)
    p.add_argument("--image-urls", type=_json_list, required=True)
    p.add_argument("--num-images", type=int, default=1)

    p = sub.add_parser("reference-group-images")
    add_image_common(p)
    p.add_argument("--image-urls", type=_json_list, required=True)
    p.add_argument("--max-images", type=int, default=4)

    return parser


def main() -> None:
    asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
