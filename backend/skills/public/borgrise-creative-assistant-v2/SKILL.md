---
name: borgrise-creative-assistant-v2
description: Use when handling Borgrise creative workflows that need file upload, video analysis, image generation, video generation, or creative ideation.
---

# Borgrise Creative Assistant v2

This skill is split into three clear modules. Pick the module by user intent and keep boundaries strict so upload, analysis, and generation do not get mixed together.

## Module Router

| User intent | Use module | Reference |
| --- | --- | --- |
| Upload source files, receive assets, check formats, create a material list | `file-upload` | `references/file-upload.md` |
| Understand an existing video, summarize content, extract shots, rhythm, selling points, captions, or problems | `video-analysis` | `references/video-analysis.md` |
| Generate creative ideas, image prompts, video concepts, storyboards, scripts, or generation parameters | `creative-generation` | `references/creative-generation.md` |

## Supporting Files

- `scripts/run_generation.py`: executable CLI for Borgrise upload, polling, 生图, 生视频, reference-mode video, long video, native-audio video, and virtual-human asset commands.
- `references/api-reference.md`: compact Borgrise API and payload reference for exact endpoint/header/body shapes.
- `references/scene-playbook.md`: prompt quality guide for product images, lifestyle images, showcase videos, reference-mode videos, native-audio videos, and long videos.
- `tests/`: zero-dependency Python tests for skill structure and high-risk generation-script behavior.

## Boundary Rules

- File upload only prepares and records materials. It does not analyze video content and does not create generation prompts.
- Video analysis only interprets existing video/material content. It does not upload files and does not produce final generation deliverables unless asked to hand off findings.
- Creative generation combines 生图, 生视频, and 创意生成 in one module because they share the same upstream brief, material insights, visual direction, and campaign goal.

## Recommended Workflow

1. Use `file-upload` when raw assets need to enter the workflow.
2. Use `video-analysis` when existing videos or uploaded materials need structured understanding.
3. Use `creative-generation` when the user needs ideas, prompts, scripts, storyboards, or generation-ready instructions.

If the user asks for an end-to-end task, run the modules in that order and label each section clearly in the final output.
