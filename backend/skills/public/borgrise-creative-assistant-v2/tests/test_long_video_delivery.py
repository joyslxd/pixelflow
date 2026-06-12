import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_generation.py"


def load_run_generation():
    spec = importlib.util.spec_from_file_location("run_generation", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LongVideoDeliveryTest(unittest.TestCase):
    def test_cumulative_extend_uses_last_video_without_merge(self):
        run_generation = load_run_generation()
        segments = [
            {"segment": 1, "video_url": "https://example.com/segment-1.mp4"},
            {"segment": 2, "video_url": "https://example.com/cumulative-20.mp4"},
            {"segment": 3, "video_url": "https://example.com/cumulative-30.mp4"},
        ]

        delivery = run_generation.select_long_video_delivery(segments)

        self.assertEqual(delivery["video_url"], "https://example.com/cumulative-30.mp4")
        self.assertFalse(delivery["merge_required"])
        self.assertEqual(delivery["merge_urls"], [])

    def test_resume_completed_progress_does_not_merge(self):
        run_generation = load_run_generation()
        with tempfile.TemporaryDirectory() as tmp:
            progress_file = Path(tmp) / "progress.json"
            progress_file.write_text(
                """
{
  "total_duration": 30,
  "segment_duration": 10,
  "segments_completed": 3,
  "total_segments": 3,
  "prompts": ["one", "two", "three"],
  "segments": [
    {"segment": 1, "video_url": "https://example.com/segment-1.mp4", "cumulative_duration": 10},
    {"segment": 2, "video_url": "https://example.com/cumulative-20.mp4", "cumulative_duration": 20},
    {"segment": 3, "video_url": "https://example.com/cumulative-30.mp4", "cumulative_duration": 30}
  ]
}
""",
                encoding="utf-8",
            )

            def fail_merge(**kwargs):
                raise AssertionError("resume should not merge cumulative extend outputs")

            result = run_generation.resume_long_reference_mode_video(
                str(progress_file),
                merge_fn=fail_merge,
                verify_fn=lambda url, expected, tolerance=2: {
                    "verified": True,
                    "verdict": "PASS",
                    "actual_duration": 30.0,
                    "expected_duration": expected,
                    "difference": 0.0,
                    "within_tolerance": True,
                },
            )

        self.assertEqual(result["video_url"], "https://example.com/cumulative-30.mp4")
        self.assertIsNone(result["merge"])
        self.assertFalse(result["delivery"]["merge_required"])


if __name__ == "__main__":
    unittest.main()
