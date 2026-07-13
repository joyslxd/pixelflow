# Seedance 全系列 Prompt Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: Use superpowers:writing-skills for the Skill changes, superpowers:test-driven-development for runtime changes, and superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前偏向 Seedance 2.0 的镜头 Prompt Skill 改造成适用于所有已启用 Seedance 系列模型的统一 Skill，并结合用户上传 Skill 中可安全复用的通用方法优化结构、约束和质量检查。

**Architecture:** 保留单一 `seedance-prompt` Skill 作为 Seedance 家族的通用策略层，运行时适配器显式接收当前 `video_model`，模型是否可用、画幅、分辨率和其他参数只服从 content-app 实时配置。Skill 只规定 PixelFlow 的跨模型不变量；模型特有能力不得硬编码为通用事实。

**Tech Stack:** Markdown Skill、Python 3.12、pytest、PixelFlow scene package adapter、content-app 动态模型配置。

## Global Constraints

- 任意 content-app 实时配置中启用且模型名包含 `seedance` 的模型都可使用此 Skill，不限定 `seedance-2.0`。
- Skill 不自行发明模型支持的画幅、清晰度、声音、多模态参考或时长能力；这些由实时模型配置和生成 API 决定。
- PixelFlow 不变量继续生效：单分镜 4–15 个整数秒、秒级时间码、一段式镜头描述、最多 9 张图片引用、只允许声明过的 `@asset_id`。
- 人物放 `characters`，商品、包装、工具和卖点物件放 `props`，环境放 `scenes`。
- 每个场景可以独立生成并最终合并；Skill 不假设供应商一次生成整条长视频。
- 上传压缩包只提炼通用创作方法，不整段复制没有清晰授权的资料。
- `THIRD_PARTY_NOTICE.md` 有来源追踪和许可证风险说明价值，必须保留并更新，不能删除。
- 新增注释、测试说明、Skill 正文、文档和提交信息使用中文；程序标识符保持英文。

---

### Task 1: 用失败测试定义 Seedance 家族级合同

**Files:**
- Modify: `backend/tests/test_seedance_prompt_skill.py:1-80`
- Modify: `backend/tests/test_video_scene_packages.py:88-125`
- Modify: `backend/skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py`
- Test: `backend/tests/test_seedance_prompt_skill.py`
- Test: `backend/tests/test_video_scene_packages.py`

**Interfaces:**
- Consumes: `build_seedance_shot_prompt(video_model=...)`。
- Produces: Skill 适用范围、当前模型透传、通用章节和第三方说明的回归合同。

- [ ] **Step 1: 把 Skill 结构测试从 2.0 改成全系列合同**

用下面测试替换 `test_vendored_seedance_skill_keeps_timestamps_and_reference_rules`：

```python
def test_vendored_seedance_skill_targets_the_whole_model_family():
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert "name: seedance-prompt" in skill_text
    assert "Seedance 系列" in skill_text
    assert "任意" in skill_text or "所有" in skill_text
    assert "仅适用于 Seedance 2.0" not in skill_text
    assert "4-15" in skill_text
    assert "秒级时间码" in skill_text
    assert "@asset_id" in skill_text
    assert "最多 9 张" in skill_text
    assert "content-app 实时配置" in skill_text
    assert (SKILL_DIR / "THIRD_PARTY_NOTICE.md").exists()
```

将 guidance 测试改为验证新通用章节：

```python
def test_load_seedance_guidance_extracts_runtime_family_rules():
    guidance = load_seedance_guidance()

    for marker in [
        "适用范围与模型边界",
        "PixelFlow 分镜执行合同",
        "参考素材与一致性",
        "声音、对白与字幕",
        "镜头语言与真实感",
        "质量检查",
    ]:
        assert marker in guidance
    assert len(guidance) < (SKILL_DIR / "SKILL.md").stat().st_size
```

- [ ] **Step 2: 为两个不同 Seedance 模型补参数化 Prompt 测试**

将现有 `test_build_seedance_shot_prompt_contains_final_contract_and_plan_context` 参数化：

```python
@pytest.mark.parametrize("video_model", ["seedance-1.5-pro", "seedance-2.0-mini"])
def test_build_seedance_shot_prompt_contains_current_model_and_final_contract(video_model: str):
    prompt = build_seedance_shot_prompt(
        scene_index=2,
        start_second=10,
        end_second=20,
        plan_markdown="## 创作目标\n展示通勤背包防泼水能力。",
        storyline="雨天通勤者从容进入办公室",
        narration="雨再大，也不怕重要文件被淋湿。",
        visual_style="电影写实，冷暖光对比",
        available_asset_ids=["character-commuter", "scene-office", "prop-backpack"],
        video_ratio="9:16",
        video_model=video_model,
    )

    assert f"当前视频模型：{video_model}" in prompt
    assert "Seedance 系列 Skill 规则" in prompt
    assert "10-20秒" in prompt
    assert "展示通勤背包防泼水能力" in prompt
    assert "@character-commuter" in prompt
    assert "@scene-office" in prompt
    assert "@prop-backpack" in prompt
    assert "最多 9" in prompt
```

增加 `import pytest`。

- [ ] **Step 3: 修改场景包测试，证明具体模型进入 LLM Prompt**

在 `test_scene_package_llm_prompt_includes_seedance_guidance_and_final_video_ratio` 的 `form_values` 增加：

```python
"video_model": "seedance-1.5-pro",
```

并将旧断言替换为：

```python
assert "Seedance 系列" in captured["prompt"]
assert "当前视频模型：seedance-1.5-pro" in captured["prompt"]
```

- [ ] **Step 4: 给 vendored 包补第三方说明合同**

在 `test_skill_structure.py` 增加：

```python
    def test_seedance_notice_records_both_sources_and_license_boundaries(self):
        notice = (ROOT / "skills" / "seedance-prompt" / "THIRD_PARTY_NOTICE.md").read_text(encoding="utf-8")

        self.assertIn("songguoxs/seedance-prompt-skill", notice)
        self.assertIn("BGEC-SD2-book-prompts-skill.zip", notice)
        self.assertIn("D1B24E9C412B95BBFB1D4CE3677EC36255E374B8A251784020FC6DE193078D94", notice)
        self.assertIn("未整段复制", notice)
        self.assertIn("MIT", notice)
```

该文件基于 `unittest.TestCase`，因此方法内使用 `self`，不要引入 pytest 风格断言。

- [ ] **Step 5: 运行测试确认 RED**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest tests/test_seedance_prompt_skill.py tests/test_video_scene_packages.py -q
py -3.13 -m uv run python skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py
```

Expected: FAIL；当前方法没有 `video_model` 参数、Skill/场景包仍写死 2.0、新 notice 未记录上传包。

- [ ] **Step 6: 提交失败测试**

```powershell
git add backend/tests/test_seedance_prompt_skill.py backend/tests/test_video_scene_packages.py backend/skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py
git commit -m "test: 锁定 Seedance 全系列 Skill 合同"
```

---

### Task 2: 重写通用 Skill 并保留可审计来源说明

**Files:**
- Replace: `backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/SKILL.md`
- Modify: `backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/THIRD_PARTY_NOTICE.md`
- Modify: `backend/skills/public/borgrise-creative-assistant-v2/SKILL.md:18-26`
- Test: `backend/skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py`

**Interfaces:**
- Produces: 面向所有 Seedance 模型的一份精简、可运行时抽取的 Skill。
- Preserves: PixelFlow 场景包 DTO 和运行时高风险约束。

- [ ] **Step 1: 用以下结构重写 `SKILL.md`**

文件应控制在约 180 行以内，保留这些准确标题，供运行时适配器抽取：

```markdown
---
name: seedance-prompt
description: 当 PixelFlow 为任意已启用的 Seedance 系列模型编写、检查或修订视频分镜镜头描述时使用；覆盖秒级时间、素材引用、镜头、声音、一致性和电商 UGC 质量检查。
---

# Seedance 系列视频分镜 Prompt Skill

## 适用范围与模型边界

- 本 Skill 面向 content-app 实时配置中所有已启用的 Seedance 系列模型，不限定某一个版本。
- 调用方必须传入当前 `video_model`；Skill 不改写用户已确认的模型。
- 画幅、清晰度、声音、参考素材类型和其他模型能力以 content-app 实时配置与生成 API 为准。
- 若某条规则与当前模型实时能力冲突，保留 PixelFlow 创作合同并由调用层提示参数不兼容，不得假设模型支持。

## PixelFlow 分镜执行合同

每个场景输入至少包括：当前模型、精确起止秒、画幅、视觉风格、故事线、旁白、可用素材、当前 plan.md。

输出必须遵守：

1. 单分镜为 4-15 个整数秒，时间范围用 `0-10秒` 这类秒级时间码。
2. 镜头描述是一整段中文；可在段内按需要细分秒级动作，但不得使用 ms、毫秒、小数时间码。
3. 内容严格承接当前 plan.md 和创作合同，不擅自改变时长、画幅、模型、商品卖点或转化目标。
4. 每个场景独立可生成；跨场景用相同资产、服装、道具、色彩和空间方向保持连续性。
5. 一段描述应覆盖主体、场所、动作、景别、运镜、光影、声音或对白、结尾状态。

推荐组织顺序：`时间范围 -> 地点与主体 -> 动作变化 -> 景别与运镜 -> 光影与风格 -> 声音或对白 -> 收束画面`。

## 参考素材与一致性

- 只使用调用方声明的 `@asset_id`，写成 `@character-x`、`@scene-x`、`@prop-x`。
- 每个分镜最多 9 张图片参考；同一素材不要重复计数。
- `characters` 只放人物；商品、包装、工具、配件和卖点物件放 `props`；环境放 `scenes`。
- 每次引用说明用途，例如“以 @prop-backpack 固定商品外观”“以 @character-commuter 固定人物身份”。
- 无引用素材时用文字完整描述，不虚构未声明的 `@asset_id`。
- 参考图决定身份和外观，文字决定本镜头动作、构图、镜头和声音；两者冲突时以创作合同与明确用户要求为准。

## 声音、对白与字幕

- 有旁白或对白时标明说话者、语气、出现秒段，并保持文本可在该秒段内自然说完。
- 无旁白时明确环境声、动作声或“本分镜无旁白”，不要凭空添加台词。
- 只有当前模型和请求参数支持声音时，声音描述才作为生成控制；否则仍可作为后期制作意图保留。
- 不要求模型直接生成可读字幕；画面文字、价格、Logo 和合规文案应使用项目已有的后期或审核策略。

## 镜头语言与真实感

- 一个短分镜只安排一个主要叙事目标，优先保证动作因果清楚。
- 景别与运镜服务信息：全景交代空间，中景表现人物与商品关系，近景或特写证明材质和卖点。
- 写清摄像机如何移动、何时停止、主体如何进入和离开画面，避免只堆砌“电影感、大片感”。
- 保持物理连续：手与商品接触合理，商品结构不突变，空间方向不跳轴，光源方向一致。
- 负向要求要具体且少量，例如避免额外人物、商品变形、手指异常、镜面文字和无关 Logo。

## 电商与 UGC 场景

- 电商镜头围绕一个可验证卖点组织“问题或场景 -> 使用动作 -> 证据 -> 结果”。
- 商品首次出现时尽快建立完整外观，后续用细节、对比、使用反馈证明价值。
- UGC 风格允许轻微手持和生活化构图，但主体、商品和关键动作必须清晰，不能用随机抖动冒充真实。
- 转化动作只能来自 plan.md，不擅自添加价格、折扣、功效承诺或平台话术。

## 质量检查

交付前逐项检查：

- 当前模型名称已传入且没有被改写。
- 起止时间、分镜时长和总合同一致；全部使用整数秒。
- 描述为一整段，包含主体、动作、镜头、光影和声音意图。
- 所有 `@asset_id` 均在可用清单中，图片引用不超过 9 张。
- 人物、场景、商品或道具分类正确，跨场景外观连续。
- 没有凭空增加卖点、价格、Logo、台词或模型能力。
- 结尾状态能与下一分镜衔接，或能独立作为最终镜头。

## 常见错误

- 把 Skill 写成只适配某个 Seedance 版本。
- 用毫秒或小数时间码，或让 10 秒镜头承载过多事件。
- 引用未声明素材、超过 9 张图片、把商品误放进人物资产。
- 只写氛围词，不写动作、构图和摄像机变化。
- 把参考图当作完整 Prompt，未说明本镜头具体动作与变化。
- 假设所有 Seedance 模型都支持同样的声音、分辨率或参考模式。

## 维护规则

- 新模型上线时优先更新 content-app 模型配置与 API 映射；通用 Skill 仅在跨模型规则变化时更新。
- 任何模型特有技巧都必须标注适用模型和可验证来源，不能提升为全系列硬规则。
- 来源与改编边界见 `THIRD_PARTY_NOTICE.md`。
```

- [ ] **Step 2: 更新 `THIRD_PARTY_NOTICE.md`，不要删除**

使用中文记录事实，至少包含：

```markdown
# 第三方来源与改编说明

本目录 `SKILL.md` 是 PixelFlow 针对运行时约束重新编写的 Seedance 系列通用 Skill。该文件吸收了下列资料中的通用概念，但未整段复制来源文本。

## 已使用来源

1. 用户先前提供的 `seedance-prompt-skill-master.zip`
   - 上游：`https://github.com/songguoxs/seedance-prompt-skill`
   - 归档 revision：`57d1e2f273747c238dd892698a05137ab2f10d4a`
   - 上游 README 声明：MIT
   - 用户归档内未包含独立 LICENSE 或版权文件，因此只记录可核验元数据。

2. 用户提供的 `BGEC-SD2-book-prompts-skill.zip`
   - SHA-256：`D1B24E9C412B95BBFB1D4CE3677EC36255E374B8A251784020FC6DE193078D94`
   - 根目录未发现统一 LICENSE/NOTICE。
   - 仅 `short-drama` 子树声明 MIT（Copyright 2025 0xsline）；其他资料没有足够清晰的许可声明。
   - PixelFlow 只提炼“主体、动作、场景、镜头、光影、声音、时间组织、参考一致性和质量检查”等通用方法，未整段复制无明确授权的正文。

## 官方能力核验来源

- Seed 模型列表：`https://seed.bytedance.com/en/models`
- Seedance 2.0 官方发布说明：`https://seed.bytedance.com/en/blog/seedance-2-0-official-launch`

模型能力仍以 content-app 实时配置和实际生成 API 为准。本说明用于保留来源链路与授权边界，不能删除或当作第三方代码许可证替代品。
```

- [ ] **Step 3: 修正父级 Skill 的路由描述**

将父级 `SKILL.md` 中：

```markdown
vendored Seedance 2.0 prompt guidance
```

改为：

```markdown
vendored Seedance-family prompt guidance used for every enabled Seedance model when building scene shot descriptions
```

- [ ] **Step 4: 运行无依赖 Skill 结构测试**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run python skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py
```

Expected: PASS。

- [ ] **Step 5: 提交 Skill 内容与 notice**

```powershell
git add backend/skills/public/borgrise-creative-assistant-v2/SKILL.md backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/SKILL.md backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/THIRD_PARTY_NOTICE.md
git commit -m "feat: 将 Seedance Prompt Skill 扩展到全系列模型"
```

---

### Task 3: 运行时显式透传当前视频模型

**Files:**
- Modify: `backend/pixelflow/generate/seedance_prompt.py:18-72`
- Modify: `backend/pixelflow/generate/scene_packages.py:230-275`
- Test: `backend/tests/test_seedance_prompt_skill.py`
- Test: `backend/tests/test_video_scene_packages.py`

**Interfaces:**
- Consumes: 表单或创作合同中的 `video_model`。
- Produces: 每个分镜 Prompt 中可审计的 `当前视频模型`，不再硬编码 2.0。

- [ ] **Step 1: 更新运行时章节提取列表**

将 `load_seedance_guidance()` 的 `sections` 改为：

```python
sections = [
    _markdown_section(source, "适用范围与模型边界", level=2),
    _markdown_section(source, "PixelFlow 分镜执行合同", level=2),
    _markdown_section(source, "参考素材与一致性", level=2),
    _markdown_section(source, "声音、对白与字幕", level=2),
    _markdown_section(source, "镜头语言与真实感", level=2),
    _markdown_section(source, "电商与 UGC 场景", level=2),
    _markdown_section(source, "质量检查", level=2),
]
```

- [ ] **Step 2: 给分镜 Prompt builder 增加必填模型参数**

在 `build_seedance_shot_prompt` keyword 参数中增加：

```python
video_model: str,
```

在现有校验后增加：

```python
normalized_video_model = str(video_model or "").strip()
if not normalized_video_model:
    raise ValueError("video_model is required for Seedance shot prompts")
```

将 guidance 标签和执行合同增加当前模型：

```python
guidance = f"Seedance 系列 Skill 规则：\n{load_seedance_guidance()}\n\n" if include_guidance else ""
```

```python
f"- 当前视频模型：{normalized_video_model}\n"
```

不要在此处写 Seedance 模型白名单，也不要把 `seedance-1.5-pro`、`seedance-2.0-mini` 等样例变成判断分支。

- [ ] **Step 3: 场景包读取实际模型并传给每个镜头合同**

在 `_build_scene_package_prompt` 读取画幅附近增加：

```python
video_model = _first_text(form_values.get("video_model"), "seedance")
```

调用 `build_seedance_shot_prompt` 时增加：

```python
video_model=video_model,
```

把 LLM 总 Prompt 的写死文本：

```python
以下是项目内 Seedance 2.0 Skill 的强制指导：
```

改为：

```python
以下是项目内 Seedance 系列 Skill 的强制指导，当前视频模型为 {video_model}：
```

- [ ] **Step 4: 运行目标测试确认 GREEN**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest tests/test_seedance_prompt_skill.py tests/test_video_scene_packages.py -q
```

Expected: PASS。

- [ ] **Step 5: 搜索残留的运行时 2.0 限定**

Run:

```powershell
rg -n "Seedance 2\.0|seedance-2\.0" backend/pixelflow/generate backend/skills/public/borgrise-creative-assistant-v2 web/src/lib/videoRequirementConfig.ts
```

Expected: 允许系统推荐默认模型、历史说明和官方来源出现 2.0；运行时 Skill 名称、guidance 标签和适用范围不能再只写 2.0。

- [ ] **Step 6: 提交运行时适配**

```powershell
git add backend/pixelflow/generate/seedance_prompt.py backend/pixelflow/generate/scene_packages.py
git commit -m "fix: 场景包按实际 Seedance 模型构建提示词"
```

---

### Task 4: 同步文档并完成 Skill 回归验证

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CONTENT_APP_API_CALLS.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Test: `backend/tests/test_seedance_prompt_skill.py`
- Test: `backend/tests/test_video_scene_packages.py`
- Test: `backend/skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py`

**Interfaces:**
- Produces: 文档明确“前端展示所有启用 Seedance，Skill 对全家族通用，能力以实时配置为准”。

- [ ] **Step 1: 更新当前事实文档**

至少写清：

```markdown
- 前端视频模型列表继续展示 content-app 返回的所有启用 Seedance 模型。
- `seedance-prompt` 是 Seedance 系列通用 Skill，不以 2.0 型号作为调用开关。
- 场景包 Prompt 显式携带用户确认的 `video_model`。
- 模型特有画幅、清晰度、声音和参考能力以 content-app 实时配置/API 为准。
- `THIRD_PARTY_NOTICE.md` 保留来源和授权边界，不能当作无用文件删除。
```

- [ ] **Step 2: 运行 Skill 和场景包完整目标测试**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest tests/test_seedance_prompt_skill.py tests/test_video_scene_packages.py tests/test_video_creation_contract.py -q
py -3.13 -m uv run python skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py
```

Expected: PASS；若合同测试文件名不同，先用 `rg --files tests | rg 'contract|video'` 选择真实文件。

- [ ] **Step 3: 运行 Python 静态检查**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run ruff check pixelflow/generate/seedance_prompt.py pixelflow/generate/scene_packages.py tests/test_seedance_prompt_skill.py tests/test_video_scene_packages.py
```

Expected: PASS。

- [ ] **Step 4: 检查差异与敏感内容**

Run:

```powershell
git diff --check
git diff --stat
rg -n "Bearer |eyJ[A-Za-z0-9_-]+\.|powermem_api_key:\s*[^$]" backend web README.md AGENTS.md CONTENT_APP_API_CALLS.md docs/pixelflow-agent-skill-flow-latest-design.md
```

Expected: diff 检查通过；敏感信息扫描没有命中本次新增内容。若仓库原有配置命中，只核对 `git diff`，绝不把值复制到日志或提交说明。

- [ ] **Step 5: 提交文档**

```powershell
git add README.md AGENTS.md CONTENT_APP_API_CALLS.md docs/pixelflow-agent-skill-flow-latest-design.md
git commit -m "docs: 更新 Seedance 全系列 Skill 说明"
```

## Completion Gate

- [ ] `seedance-1.5-pro` 和 `seedance-2.0-mini` 均通过同一个 Prompt builder 测试。
- [ ] 场景包 LLM Prompt 包含真实 `video_model`，不再写死 Seedance 2.0。
- [ ] Skill 保留所有 PixelFlow 高风险约束，且不硬编码模型特有能力。
- [ ] `THIRD_PARTY_NOTICE.md` 保留并同时记录两个输入来源、哈希和授权边界。
- [ ] vendored Skill 结构测试、Seedance adapter 测试、scene package 测试和 ruff 全部通过。
