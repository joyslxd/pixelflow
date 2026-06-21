# PixelFlow Creative Brief Skill Optimization

This is the first SkillOpt pilot for PixelFlow. It trains the instructions that
guide CREATIVE Brief generation, while keeping production code untouched.

## Target

Optimize a 300-2000 token skill/prompt document that tells the CREATIVE model how
to produce PixelFlow Brief objects.

The optimized skill should improve:

- first-shot hook compliance
- CTA ending compliance
- target-duration fit
- generation prompt cleanliness, especially no rendered subtitles/text
- product anchoring in real-asset shots
- reference-mode strategy use
- concise narration and on-screen copy

## Validation Gate

The deterministic gate lives in:

`backend/pixelflow/evals/creative_brief/scorer.py`

It reuses `pixelflow.creative.validator.validate_and_fix()` and adds business
checks that are stable enough for offline validation. A candidate Brief passes
only when:

- score is at least `0.90`
- the existing validator needs no fixes and emits no warnings
- product/reference/duration/prompt-cleanliness checks pass

## Dataset Shape

Samples are JSON objects with:

```json
{
  "id": "thermos_original_15s",
  "creative_mode": "original",
  "product_info": {},
  "video_params": {},
  "creative_direction": "",
  "reference_analysis": null
}
```

Keep train/val/test split stable. SkillOpt can train on `train`, accept edits
only on `val`, and report final quality on `test`.

