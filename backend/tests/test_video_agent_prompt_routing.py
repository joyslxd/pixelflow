"""系统提示只做 Tool 路由；STAGE 精华在 Tool 执行 Prompt。"""

from __future__ import annotations

from pixelflow.video_agent.prompts import VIDEO_AGENT_SYSTEM_PROMPT
from pixelflow.video_agent.tools.script import ImportScriptTool
from pixelflow.video_agent.tools.script_skill_pipeline import (
    STAGE_PROMPTS,
    RunScriptSkillStageTool,
    _stage_system_prompt,
    build_import_structure_system_prompt,
)


def test_system_prompt_routes_tools_without_eight_stage_pipeline() -> None:
    prompt = VIDEO_AGENT_SYSTEM_PROMPT
    assert "import_script" in prompt
    assert "run_script_skill_stage" in prompt
    assert "polish_seedance_shot_prompts" in prompt
    assert "prepare_scene_packages" in prompt
    assert "generate_scene_assets" in prompt
    assert "重新生成视频分镜包" in prompt
    assert "如何选 Tool" in prompt
    assert "八阶段" in prompt  # 明确禁止重演
    assert "禁止" in prompt
    assert "registered_scene_asset_image_models" in prompt
    assert "gpt-image-2" in prompt
    assert "seeddream-5.0" in prompt
    assert "Midjourney" in prompt  # 作为禁止项出现
    # 不得把 STAGE 写作细则塞进系统提示
    assert "爽点矩阵" not in prompt
    assert "五维自检" not in prompt
    assert STAGE_PROMPTS["characters"] not in prompt
    # outline 阶段已并入其它键；有则校验系统提示不得内嵌写作细则。
    for stage_key in ("outline", "episode"):
        stage_prompt = STAGE_PROMPTS.get(stage_key)
        if stage_prompt:
            assert stage_prompt not in prompt
    # Skill 长规则不得进系统提示
    assert "PixelFlow 分镜执行合同" not in prompt


def test_polish_tool_description_routes_not_writes() -> None:
    from pixelflow.video_agent.tools.seedance_polish import PolishSeedanceShotPromptsTool

    desc = PolishSeedanceShotPromptsTool.spec.description
    assert "seedance-prompt" in desc
    assert "prepare_scene_packages" in desc
    assert "import_script" in desc
    assert "地点：" not in desc  # 写作细则在执行 Prompt，不在 Registry 文案


def test_import_structure_prompt_sinks_multi_stage_prompts() -> None:
    prompt = build_import_structure_system_prompt()
    assert "## 角色/场景/道具设定" in prompt
    assert "## 剧本正文" in prompt
    assert "## 五维自检" in prompt
    assert "## 合规检查" in prompt
    assert "## 导出终稿" in prompt
    assert STAGE_PROMPTS["characters"] in prompt
    assert STAGE_PROMPTS["episode"] in prompt
    assert STAGE_PROMPTS["review"] in prompt
    assert STAGE_PROMPTS["compliance"] in prompt
    assert STAGE_PROMPTS["export"] in prompt
    assert "实体命名硬约束" in prompt
    # import 拆解的「剧本正文」须与 run_script_skill_stage(episode) 同灌 bgrs
    assert "【bgrs Skill 写作指导摘录】" in prompt
    assert "| 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | 屏幕文案 | 行动引导 |" in prompt
    assert "禁止输出 Skill 原文的 △" in prompt
    # outline 已并入其它阶段；若仍存在则不得缺失设定约束
    if "outline" in STAGE_PROMPTS:
        assert STAGE_PROMPTS["outline"] in prompt or "## 分镜大纲" not in prompt


def test_stage_system_prompt_includes_stage_task() -> None:
    characters = _stage_system_prompt("characters")
    assert STAGE_PROMPTS["characters"] in characters
    assert "实体命名硬约束" in characters

    start = _stage_system_prompt("start")
    assert STAGE_PROMPTS["start"] in start
    assert "实体命名硬约束" not in start


def test_episode_stage_prompt_loads_bgrs_skill_with_six_column_contract() -> None:
    """首次 /episode 须注入 bgrs Skill 摘录，并强制六列表而非 △ 格式。"""

    from pixelflow.video_agent.skills.bgrs_episode_guidance import (
        build_episode_six_column_contract,
        load_bgrs_episode_guidance,
    )

    guidance = load_bgrs_episode_guidance()
    assert "铁律 1" in guidance
    assert "视听语言" in guidance
    assert "景别分层" in guidance

    prompt = _stage_system_prompt("episode")
    assert STAGE_PROMPTS["episode"] in prompt
    assert build_episode_six_column_contract() in prompt
    assert "【bgrs Skill 写作指导摘录】" in prompt
    assert "| 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | 屏幕文案 | 行动引导 |" in prompt
    assert "禁止输出 Skill 原文的 △" in prompt
    # Skill 正文进 Tool 执行 Prompt，不得误以为只剩短 STAGE 句
    assert "铁律 11" in prompt or "视听语言优先" in prompt


def test_tool_descriptions_explain_when_not_how_to_write() -> None:
    import_desc = ImportScriptTool.spec.description
    assert "markdown" in import_desc
    assert "run_script_skill_stage" in import_desc

    stage_desc = RunScriptSkillStageTool.spec.description
    assert "不是必须跑完八阶段" in stage_desc
    assert "import_script" in stage_desc
    assert "characters" in stage_desc
