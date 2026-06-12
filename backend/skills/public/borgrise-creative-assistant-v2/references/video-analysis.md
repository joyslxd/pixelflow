# Video Analysis Module

## Purpose

Analyze existing video materials and turn them into structured creative understanding.

## Use When

- The user wants to understand a video before making new creative assets.
- The user asks for shot breakdown, pacing, hook, selling points, product moments, subtitles, audio, CTAs, or competitor-video analysis.
- The workflow needs reusable insights before 生图, 生视频, or 创意生成.

## Inputs

- Existing video file or video link.
- Optional analysis goal: ad optimization, creative reuse, localization, platform adaptation, product extraction, or script rewrite.

## Outputs

- High-level summary.
- Shot-by-shot or segment-by-segment structure.
- Key selling points, audience pain points, proof points, and CTA.
- Visual style, pacing, audio/subtitle notes.
- Improvement suggestions and reusable creative insights.
- Handoff note for `creative-generation`.

## Boundary

This module **不负责文件上传** and does not create final generation prompts as its main output. If generation is needed, summarize findings and hand them to `creative-generation`.

## Checklist

1. Clarify the analysis goal.
2. Break the video into meaningful segments.
3. Extract message, product, audience, and platform signals.
4. Identify strengths, weaknesses, and reusable creative assets.
5. Produce a structured handoff for generation.
