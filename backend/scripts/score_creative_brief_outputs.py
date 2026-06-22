"""Score generated Creative Brief outputs for the SkillOpt pilot.

Input is JSONL. Each line can either contain:

  {"id": "...", "product_info": {...}, "video_params": {...},
   "creative_mode": "original", "brief": {...}}

or:

  {"sample": {...}, "brief": {...}}

The script prints a JSON summary and exits non-zero when any item fails the
validation gate. It intentionally does not call an LLM; rollout generation stays
separate from deterministic validation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pixelflow.evals.creative_brief import score_brief


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return rows


def _sample_and_brief(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sample = row.get("sample") if isinstance(row.get("sample"), dict) else row
    brief = row.get("brief")
    if not isinstance(brief, dict):
        raise SystemExit(f"row {row.get('id') or sample.get('id') or '<unknown>'}: missing object field 'brief'")
    return sample, brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score PixelFlow Creative Brief JSONL outputs.")
    parser.add_argument("outputs", type=Path, help="JSONL file containing generated Brief outputs")
    parser.add_argument("--min-mean-score", type=float, default=0.9)
    args = parser.parse_args(argv)

    rows = _load_jsonl(args.outputs)
    results = []
    for row in rows:
        sample, brief = _sample_and_brief(row)
        scored = score_brief(
            brief,
            product_info=sample.get("product_info"),
            video_params=sample.get("video_params"),
            creative_mode=sample.get("creative_mode", "original"),
        )
        results.append(
            {
                "id": sample.get("id"),
                "score": scored.score,
                "passed": scored.passed,
                "metrics": scored.metrics,
                "issues": scored.issues,
            }
        )

    mean_score = round(sum(r["score"] for r in results) / len(results), 4) if results else 0.0
    summary = {
        "count": len(results),
        "passed": all(r["passed"] for r in results) and mean_score >= args.min_mean_score,
        "mean_score": mean_score,
        "min_mean_score": args.min_mean_score,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
