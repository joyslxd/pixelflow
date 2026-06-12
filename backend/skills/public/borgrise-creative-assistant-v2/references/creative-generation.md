# Creative Generation Module

## Purpose

Combine 生图, 生视频, and 创意生成 into one module for Borgrise creative output. These tasks share the same brief, material insights, visual direction, and campaign goal.

## Use When

- The user wants new creative ideas, ad angles, campaign concepts, visual directions, image prompts, video prompts, scripts, storyboards, or generation settings.
- The user asks to turn uploaded materials or video analysis into new content.
- The task includes 生图, 生视频, 创意生成, or any combination of the three.

## Inputs

- Creative brief: product, target audience, platform, goal, tone, language, and restrictions.
- Optional materials from `file-upload`.
- Optional insights from `video-analysis`.

## Outputs

- Creative angles and concept options.
- Image generation prompts with subject, scene, style, composition, lighting, aspect ratio, and negative constraints.
- Video generation prompts with scene sequence, motion, camera, duration, pacing, transitions, and audio/subtitle direction.
- Script, storyboard, or shot list when the user needs production-ready structure.
- Selection recommendation with rationale.

## Execution References

- Use `scene-playbook.md` before writing final prompts so the scene structure matches the intended output type.
- Use `api-reference.md` when checking endpoint names, required headers, payload fields, ratios, durations, and project ID behavior.
- Use `../scripts/run_generation.py` for actual Borgrise execution after the prompt or script is approved.

## Boundary

This module owns **生图**, **生视频**, and **创意生成**. It should not spend time on file intake details or deep source-video analysis unless those results are already provided or the user asks for a quick check.

## Checklist

1. Restate the creative goal and output format.
2. Use uploaded materials and analysis findings as constraints.
3. Generate 2-3 creative directions when the user has not specified one.
4. Produce generation-ready prompts or scripts.
5. Add practical notes: aspect ratio, duration, platform fit, risks, and next action.
