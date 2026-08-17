"""为 /episode 剧本正文加载 bgrs（BGEC-SD2）Skill 写作指导摘录。

完整 Skill 过长，运行时只抽取与「可拍摄镜头正文」相关的铁律与镜头语言章节；
输出格式仍由 PixelFlow 六列合同约束，不采用 Skill 原文的 △ 文学剧本格式。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_BGRS_SKILL_ROOT = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "public"
    / "borgrise-creative-assistant-v2"
    / "skills"
    / "sedance-video-prompts-skill"
)
BGRS_SKILL_PATH = _BGRS_SKILL_ROOT / "SKILL.md"
BGRS_CINEMATIC_PATH = _BGRS_SKILL_ROOT / "references" / "cinematic-techniques.md"

# Skill.md 中与可拍镜头正文直接相关的章节（相对全量 Skill 的运行时摘录）。
_SKILL_SECTIONS: tuple[tuple[int, str], ...] = (
    (3, "铁律 1：时长与分段计算"),
    (3, "铁律 4：对白排版（配音对齐关键）"),
    (3, "铁律 5：审核规避（正向描述代替负面词）"),
    (3, "铁律 11：视听语言优先（电影感的真正杠杆）"),
    (3, "铁律 14：叙事定时长 · 分段打包 · 切镜头衔接（多镜头短片分段铁律）"),
    (2, "脚本写作指南"),
    (2, "质量保证"),
)

_CINEMATIC_SECTIONS: tuple[str, ...] = (
    "二、景别分层：拆开信息，别塞进一个镜头",
    "三、16 种运镜 → 情绪映射（运镜是为情绪服务，不是为了“动”）",
    "速查总表（写 prompt 时按需取用）",
)


def _markdown_section(source: str, heading: str, *, level: int) -> str:
    marker = "#" * level
    match = re.search(
        rf"(?ms)^{re.escape(marker)}\s+{re.escape(heading)}\s*$.*?(?=^{'#' * level}\s+|\Z)",
        source,
    )
    return match.group(0).strip() if match else ""


@lru_cache(maxsize=1)
def load_bgrs_episode_guidance() -> str:
    """加载 bgrs Skill 中服务 /episode 写作的摘录。

    缺文件或关键章节缺失时抛错，避免静默退回空指导。
    """

    if not BGRS_SKILL_PATH.is_file():
        raise FileNotFoundError(f"bgrs Skill 不存在: {BGRS_SKILL_PATH}")
    skill_source = BGRS_SKILL_PATH.read_text(encoding="utf-8")
    parts: list[str] = []
    for level, heading in _SKILL_SECTIONS:
        section = _markdown_section(skill_source, heading, level=level)
        if section:
            parts.append(section)
    if BGRS_CINEMATIC_PATH.is_file():
        cine_source = BGRS_CINEMATIC_PATH.read_text(encoding="utf-8")
        for heading in _CINEMATIC_SECTIONS:
            section = _markdown_section(cine_source, heading, level=2)
            if section:
                parts.append(section)
    guidance = "\n\n".join(parts).strip()
    if len(guidance) < 800:
        raise ValueError(f"bgrs episode 指导摘录过短或缺失: {BGRS_SKILL_PATH}")
    return guidance


def build_episode_six_column_contract() -> str:
    """PixelFlow 强制输出合同：六列表，覆盖 Skill 原文 △ 格式。"""

    return (
        "【PixelFlow 输出合同·强制】\n"
        "1) 禁止输出 Skill 原文的 △ 文学剧本格式、场次标题块或纯散文剧本。\n"
        "2) 必须输出 Markdown 镜头列表表格，表头精确为：\n"
        "| 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | 屏幕文案 | 行动引导 |\n"
        "3) 时间列用整数秒区间（如 0-10秒、10-20秒）；单镜时长优先 4-15 秒，总和覆盖上游时长。\n"
        "4) 景别/运镜列落实 Skill 视听语言（中景/近景/特写、推/拉/摇/移/跟等），不要空泛写「镜头移动」。\n"
        "5) 画面列必须可拍摄：地点、主体、动作、光影、收束；引用设定集实体写成 @实体名"
        "（如 @安然、@会议室、@氧气防晒），禁止只写裸名。\n"
        "6) 旁白/对白列写说话内容；屏幕文案列写片上字；行动引导列写 CTA 或「无」。\n"
        "7) 开头可有一行「时长：N秒 画幅：…」元信息，随后紧跟「镜头列表：」与表格。\n"
        "8) Skill 铁律用于提升可拍性与节奏，但交付形态以本六列合同为准。"
    )


__all__ = [
    "BGRS_SKILL_PATH",
    "build_episode_six_column_contract",
    "load_bgrs_episode_guidance",
]
