import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_generation.py"


def load_run_generation():
    spec = importlib.util.spec_from_file_location("run_generation", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeAudioVideoTest(unittest.TestCase):
    def test_native_audio_prompt_adds_generation_contract_without_losing_dialogue(self):
        run_generation = load_run_generation()
        prompt = "镜头1：办公室。女1：“你试试这个善存新一代小紫瓶。” 女2：“当然。”"

        native_prompt = run_generation.build_native_audio_prompt(prompt)

        self.assertIn("生成一条自然中文口播短视频", native_prompt)
        self.assertIn("不要字幕", native_prompt)
        self.assertIn("不要念出时间码、标签名或技术要求", native_prompt)
        self.assertIn("女1：“你试试这个善存新一代小紫瓶。”", native_prompt)
        self.assertIn("女2：“当然。”", native_prompt)

    def test_native_audio_reference_video_wraps_reference_mode_with_sound_on(self):
        run_generation = load_run_generation()
        calls = []

        def fake_reference_mode_video(**kwargs):
            calls.append(kwargs)
            return {"success": True, "video_url": "https://example.com/native-audio.mp4"}

        result = run_generation.native_audio_reference_video(
            prompt="女1：“当然。”",
            image_urls=["https://example.com/product.png"],
            duration=10,
            reference_video_fn=fake_reference_mode_video,
        )

        self.assertEqual(result["video_url"], "https://example.com/native-audio.mp4")
        self.assertEqual(calls[0]["sound"], "on")
        self.assertEqual(calls[0]["model"], "seedance-2.0")
        self.assertIn("自然中文口播短视频", calls[0]["prompt"])
        self.assertIn("不要念出时间码、标签名或技术要求", calls[0]["prompt"])

    def test_long_native_audio_reference_video_wraps_each_segment_prompt(self):
        run_generation = load_run_generation()
        calls = []

        def fake_long_reference_mode_video(**kwargs):
            calls.append(kwargs)
            return {"success": True, "video_url": "https://example.com/long-native-audio.mp4"}

        result = run_generation.long_native_audio_reference_video(
            prompts=["0-10s：女1：“开始。”", "10-20s：旁白：“产品定格。”"],
            image_urls=["https://example.com/product.png"],
            total_duration=20,
            segment_duration=10,
            long_reference_video_fn=fake_long_reference_mode_video,
        )

        self.assertEqual(result["video_url"], "https://example.com/long-native-audio.mp4")
        self.assertEqual(calls[0]["sound"], "on")
        self.assertEqual(calls[0]["total_duration"], 20)
        self.assertEqual(len(calls[0]["prompts"]), 2)
        self.assertTrue(all("自然中文口播短视频" in prompt for prompt in calls[0]["prompts"]))
        self.assertTrue(all("不要念出时间码、标签名或技术要求" in prompt for prompt in calls[0]["prompts"]))


if __name__ == "__main__":
    unittest.main()
