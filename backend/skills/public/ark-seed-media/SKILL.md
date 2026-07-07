---
name: ark-seed-media
description: Use when generating images or videos with Volcengine Ark Plan visual models via skill scripts, without tool wrapping.
---

# Ark Seed Media

Use this skill for atomic media generation with Volcengine Ark:

- Seedance video generation: text-to-video, image-to-video, all-purpose reference-to-video.
- Seedream image generation: text-to-image, image-to-image, multi-image reference group generation.

## Environment

Set one of:

- `ARK_PLAN_API_KEY`
- `ARK_API_KEY`
- `VOLCENGINE_ARK_API_KEY`

Optional overrides:

- `ARK_BASE_URL`, default `https://ark.cn-beijing.volces.com/api/plan/v3`
- `ARK_SEEDANCE_MODEL`, default `doubao-seedance-2.0`
  - available: `doubao-seedance-2.0`, `doubao-seedance-2.0-fast`, `doubao-seedance-2.0-mini`, `doubao-seedance-1.5-pro`
- `ARK_SEEDREAM_MODEL`, default `doubao-seedream-5.0-lite`
- `ARK_SEEDANCE_RESOLUTION`, default `720p`
- `ARK_POLL_INTERVAL`, default `5`
- `ARK_POLL_TIMEOUT`, default `600`

Medium packages do not support Seedance 2.0 series models. Choose an account-supported model before running video generation.

## Script

Run commands from the backend directory so the `pixelflow` package is importable:

```bash
cd backend
uv run python -m pixelflow.skills.ark_seed.run_seed_media text-to-video --prompt "A product hero shot..." --duration 5 --ratio 9:16
```

The script prints normalized JSON:

```json
{
  "ok": true,
  "url": "https://...",
  "urls": ["https://..."],
  "task_id": "..."
}
```

## Video Commands

Text-to-video:

```bash
uv run python -m pixelflow.skills.ark_seed.run_seed_media text-to-video \
  --prompt "A cinematic product video..." \
  --duration 5 \
  --ratio 9:16 \
  --generate-audio
```

Image-to-video:

```bash
uv run python -m pixelflow.skills.ark_seed.run_seed_media image-to-video \
  --image-url "https://example.com/product.png" \
  --prompt "The product rotates slowly with premium lighting." \
  --duration 5 \
  --ratio 9:16 \
  --generate-audio
```

All-purpose reference-to-video:

```bash
uv run python -m pixelflow.skills.ark_seed.run_seed_media reference-to-video \
  --prompt "Follow the reference motion and keep the product identity stable." \
  --image-urls '["https://example.com/product.png"]' \
  --video-urls '["https://example.com/reference.mp4"]' \
  --audio-urls '[]' \
  --duration 5 \
  --ratio 9:16 \
  --generate-audio
```

## Image Commands

Text-to-image:

```bash
uv run python -m pixelflow.skills.ark_seed.run_seed_media text-to-image \
  --prompt "A clean e-commerce product poster..." \
  --size 2K \
  --ratio 1:1 \
  --num-images 1
```

Image-to-image:

```bash
uv run python -m pixelflow.skills.ark_seed.run_seed_media image-to-image \
  --image-urls '["https://example.com/source.png"]' \
  --prompt "Keep the product unchanged, place it in a bright kitchen." \
  --size 2K \
  --ratio 1:1 \
  --num-images 1
```

Multi-image reference group generation:

```bash
uv run python -m pixelflow.skills.ark_seed.run_seed_media reference-group-images \
  --image-urls '["https://example.com/a.png","https://example.com/b.png"]' \
  --prompt "Generate a coherent 4-image product lifestyle group." \
  --size 2K \
  --ratio 1:1 \
  --max-images 4
```

## Notes

- Prefer public HTTPS URLs for reference assets.
- Keep video duration within the model/account limits configured in Ark.
- If Ark changes an endpoint or model alias, override `ARK_BASE_URL`, `ARK_SEEDANCE_MODEL`, or `ARK_SEEDREAM_MODEL` instead of editing prompts.
