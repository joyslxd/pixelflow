from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SkillStructureTest(unittest.TestCase):
    def test_skill_has_router_and_three_clear_modules(self):
        expected_files = [
            ROOT / "SKILL.md",
            ROOT / "scripts" / "run_generation.py",
            ROOT / "references" / "file-upload.md",
            ROOT / "references" / "video-analysis.md",
            ROOT / "references" / "creative-generation.md",
            ROOT / "references" / "api-reference.md",
            ROOT / "references" / "scene-playbook.md",
        ]

        for path in expected_files:
            self.assertTrue(
                path.exists(),
                f"Missing expected skill file: {path.relative_to(ROOT)}",
            )

    def test_modules_keep_upload_analysis_and_generation_boundaries(self):
        router = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        file_upload = (ROOT / "references" / "file-upload.md").read_text(encoding="utf-8")
        video_analysis = (ROOT / "references" / "video-analysis.md").read_text(encoding="utf-8")
        creative_generation = (ROOT / "references" / "creative-generation.md").read_text(encoding="utf-8")

        self.assertIn("file-upload", router)
        self.assertIn("video-analysis", router)
        self.assertIn("creative-generation", router)

        self.assertIn("不做视频分析", file_upload)
        self.assertIn("不负责文件上传", video_analysis)
        self.assertIn("生图", creative_generation)
        self.assertIn("生视频", creative_generation)
        self.assertIn("创意生成", creative_generation)

    def test_generation_script_exposes_creative_generation_commands(self):
        script = (ROOT / "scripts" / "run_generation.py").read_text(encoding="utf-8")

        for command in [
            "text-to-image",
            "reference-image",
            "image-edit",
            "text-to-video",
            "image-to-video",
            "reference-mode-video",
            "native-audio-reference-video",
            "long-reference-mode-video",
            "resume-long-reference-mode-video",
            "create-virtual-human-asset",
            "upload-file",
            "resolve-assets",
        ]:
            self.assertIn(command, script)

    def test_generation_script_help_lists_delivery_commands(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_generation.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        for command in [
            "image-to-video",
            "text-to-video",
            "reference-mode-video",
            "native-audio-reference-video",
            "long-reference-mode-video",
            "text-to-image",
            "reference-image",
            "upload-file",
            "resolve-assets",
        ]:
            self.assertIn(command, result.stdout)

    def test_supporting_references_are_exposed_from_router(self):
        router = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        for resource in [
            "scripts/run_generation.py",
            "references/api-reference.md",
            "references/scene-playbook.md",
            "tests/",
        ]:
            self.assertIn(resource, router)

    def test_package_has_no_generated_or_empty_delivery_artifacts(self):
        for cache_dir in ROOT.rglob("__pycache__"):
            shutil.rmtree(cache_dir)

        forbidden = []
        empty_dirs = []

        for path in ROOT.rglob("*"):
            if path.name == ".DS_Store" or path.name == "__pycache__" or path.suffix == ".pyc":
                forbidden.append(path.relative_to(ROOT))
            if path.is_dir() and not any(path.iterdir()):
                empty_dirs.append(path.relative_to(ROOT))

        self.assertEqual(forbidden, [])
        self.assertEqual(empty_dirs, [])


if __name__ == "__main__":
    unittest.main()
