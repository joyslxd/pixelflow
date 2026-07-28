# 视频分镜提示词去重与优先级压缩实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让视频分镜最终提示词只由结构化字段组装一次，完整保留镜头信息，并移除 PixelFlow 的 2500 字符硬限制。

**Architecture:** `scene_packages.py` 继续生成故事线、镜头描述、旁白、转场和视觉风格等结构化数据，但新场景包的 `prompt` 仅保存视觉风格兼容值。`pixelflow_video.py` 的 `_build_scene_video_prompt()` 成为唯一最终组装器，优先使用 `VideoCreationContract.visual_style`，对历史复合 `scene.prompt` 只提取视觉风格，再按固定顺序生成供应商提示词。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、pytest、httpx TestClient

## Global Constraints

- 最终提示词顺序固定为：视觉风格、故事线、镜头描述、旁白、转场。
- 地点、主体、动作、景别、运镜、光影、声音和收束信息不得被截断。
- 不增加 LLM 调用，不做模糊语义去重，不使用固定字符切片。
- 删除 Python 端 2500 字符硬限制，保留模型、时长和参考素材能力校验。
- 新增或修改的人工注释、docstring 和提交信息必须使用中文。
- 不修改 content-app 仓库。

---

### Task 1: 固定最终提示词的唯一组装行为

**Files:**
- Modify: `backend/tests/test_pixelflow_video_router.py`
- Modify: `backend/tests/test_agent_video_workflow_generation.py`
- Modify: `backend/app/gateway/routers/pixelflow_video.py`
- Modify: `backend/pixelflow/agent_workflows/video/video_generation.py`

**Interfaces:**
- Consumes: `SceneGenerationItem`、`VideoCreationContract.visual_style`
- Produces: `_build_scene_video_prompt(scene: SceneGenerationItem, *, visual_style: str = "") -> str`
- Produces: `_extract_legacy_visual_style(prompt: str) -> str`
- Produces: `_normalize_scene_prompt_field(value: str, label: str) -> str`

- [ ] **Step 1: 写入固定顺序和不重复拼接的失败测试**

在 `backend/tests/test_pixelflow_video_router.py` 增加测试，构造一个旧复合 `scene.prompt`：

```python
def test_scene_video_prompt_uses_structured_fields_once_in_fixed_order() -> None:
    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=6000,
        prompt="故事线：雨滴落在背包表面。\n镜头描述：旧镜头。\n视觉风格：电影写实。\n旁白：旧旁白。",
        storyline="雨滴落在背包表面。",
        shot_description={"text": "0-6秒：地点：地铁口；主体：通勤者；动作：抬起背包；景别：中景；运镜：缓慢推进；光影：清晨逆光；声音：雨声；收束：定格品牌标识。"},
        narration="下雨也能从容通勤。",
        transition="顺着雨滴方向切到拉链特写。",
    )

    prompt = _build_scene_video_prompt(scene, visual_style="高级电影写实")

    assert prompt.splitlines() == [
        "视觉风格：高级电影写实",
        "故事线：雨滴落在背包表面。",
        "镜头描述：0-6秒：地点：地铁口；主体：通勤者；动作：抬起背包；景别：中景；运镜：缓慢推进；光影：清晨逆光；声音：雨声；收束：定格品牌标识。",
        "旁白：下雨也能从容通勤。",
        "转场：顺着雨滴方向切到拉链特写。",
    ]
    assert prompt.count("雨滴落在背包表面。") == 1
```

- [ ] **Step 2: 写入历史视觉风格兼容和标签去重的失败测试**

```python
def test_scene_video_prompt_extracts_only_visual_style_from_legacy_prompt() -> None:
    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=4000,
        prompt="故事线：旧故事；镜头描述：旧镜头；视觉风格：冷调写实；旁白：旧旁白",
        storyline="新故事",
        shot_description={"text": "镜头描述：地点：实验室；主体：研究员；动作：观察样本；景别：近景；运镜：固定；光影：冷白光；声音：仪器声；收束：样本进入焦点。"},
        narration="旁白：观察微观变化。",
        transition="转场：淡出。",
    )

    prompt = _build_scene_video_prompt(scene)

    assert prompt.startswith("视觉风格：冷调写实")
    assert "旧故事" not in prompt
    assert "镜头描述：镜头描述：" not in prompt
    assert "旁白：旁白：" not in prompt
    assert "转场：转场：" not in prompt
```

- [ ] **Step 3: 运行测试并确认 RED**

Run:

```bash
cd backend
uv run pytest tests/test_pixelflow_video_router.py -k "scene_video_prompt_uses_structured_fields_once_in_fixed_order or scene_video_prompt_extracts_only_visual_style_from_legacy_prompt" -v
```

Expected: FAIL，因为 `_build_scene_video_prompt()` 尚不接受 `visual_style`，并且仍会整体拼接旧 `scene.prompt`。

- [ ] **Step 4: 实现最小的确定性组装器**

在 `backend/app/gateway/routers/pixelflow_video.py`：

```python
def _build_scene_video_prompt(
    scene: SceneGenerationItem,
    *,
    visual_style: str = "",
) -> str:
    style = _normalize_scene_prompt_field(visual_style, "视觉风格")
    if not style:
        style = _extract_legacy_visual_style(scene.prompt)
    fields = (
        ("视觉风格", style),
        ("故事线", scene.storyline),
        ("镜头描述", _shot_description_text(scene.shot_description)),
        ("旁白", scene.narration),
        ("转场", scene.transition),
    )
    return "\n".join(
        f"{label}：{normalized}"
        for label, value in fields
        if (normalized := _normalize_scene_prompt_field(value, label))
    )
```

辅助函数只允许清理首尾空白、重复同名标签、连续空白和完全相同的连续行；镜头描述文本不得按长度截断。`_extract_legacy_visual_style()` 只提取旧复合文本的视觉风格段；检测到故事线、镜头描述、旁白或转场标签但没有视觉风格时返回空字符串。

在 `_generate_scene_videos_response()` 中改为：

```python
prompt = _build_scene_video_prompt(
    scene,
    visual_style=contract.visual_style if contract is not None else "",
)
```

- [ ] **Step 5: 运行提示词测试并确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/test_pixelflow_video_router.py -k "scene_video_prompt" -v
```

Expected: 所有 `scene_video_prompt` 测试通过，八类镜头信息保持完整。

- [ ] **Step 6: 提交第一组代码**

```bash
git add backend/app/gateway/routers/pixelflow_video.py backend/tests/test_pixelflow_video_router.py
git commit -m "修复：统一组装视频分镜提示词" -m "移除旧复合提示词的重复拼接，并按视觉风格、故事线、镜头描述、旁白和转场的固定顺序生成最终提示词。"
```

### Task 2: 兼容历史纯 prompt 与 R1 冻结执行快照

**Files:**
- Modify: `backend/tests/test_pixelflow_video_router.py`
- Verify: `backend/pixelflow/generate/scene_packages.py`
- Verify: `backend/pixelflow/agent_workflows/video/scene_packages.py`

**Interfaces:**
- Consumes: 历史只有 `scene.prompt` 的 v2 请求
- Preserves: R1 `scene.prompt` 冻结执行快照及签名校验
- Produces: 有结构化字段时重新组装、无结构化字段时原样使用旧 prompt

- [ ] **Step 1: 写入历史纯 prompt 原样执行的回归测试**

使用已有只传 `prompt` 和参考图的场景视频测试，断言 Fake Skill 收到原始文本：

```python
assert calls[0]["prompt"] == "第一幕展示白色耳机"
```

- [ ] **Step 2: 运行目标测试并确认 RED**

Run:

```bash
cd backend
uv run pytest tests/test_pixelflow_video_router.py -k "generates_scene_videos" -v
```

Expected: 在组装器首次改造后 FAIL，因为纯 prompt 被错误解释为视觉风格并添加标签。

- [ ] **Step 3: 实现历史纯 prompt 兼容**

在 `_build_scene_video_prompt()` 开头检测结构化字段：

```python
shot_text = _shot_description_text(scene.shot_description)
if not any((scene.storyline, shot_text, scene.narration, scene.transition)):
    return str(scene.prompt or "").strip()
```

不得修改 `scene_packages.py` 的 R1 权威 prompt 生成，也不得修改
`agent_workflows/video/scene_packages.py` 对冻结执行快照的逐字校验。

- [ ] **Step 4: 运行 v2 与 R1 场景包测试并确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/test_pixelflow_video_router.py tests/test_video_scene_packages.py tests/test_agent_video_workflow_generation.py -v
```

Expected: v2 历史请求保持原 prompt；R1 冻结 prompt、恢复签名和防篡改测试全部通过。

- [ ] **Step 5: 提交兼容修复**

```bash
git add backend/app/gateway/routers/pixelflow_video.py backend/tests/test_pixelflow_video_router.py
git commit -m "修复：兼容历史分镜提示词合同" -m "有结构化字段时统一组装，历史纯 prompt 和 R1 冻结执行快照保持原有语义。"
```

### Task 3: 删除 Python 2500 字符硬限制并同步合同文档

**Files:**
- Modify: `backend/tests/test_pixelflow_video_router.py`
- Modify: `backend/app/gateway/routers/pixelflow_video.py`
- Modify: `CONTENT_APP_API_CALLS.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`（仅当检索到 2500 字符说明）

**Interfaces:**
- Preserves: `_validate_scene_video_request()` 的模型、时长和参考素材能力校验
- Produces: 超过 2500 字符且其他参数合法的请求进入 `VideoGenerationSkill`

- [ ] **Step 1: 写入超长提示词进入 Skill 的失败测试**

使用 FastAPI TestClient 和假 Video Skill，构造合法 `text_to_video` 请求，其中镜头描述超过 2500 字符：

```python
long_shot = "地点：演播室；主体：产品；动作：旋转展示；景别：近景；运镜：环绕；光影：轮廓光；声音：节奏音乐；收束：品牌标识定格。" * 30
```

断言响应成功、Fake Skill 被调用一次，并且收到的 `prompt` 长度大于 2500。另在统一
视频工作流中直接验证 `_generation_requests()` 接受同类超长权威 prompt。

- [ ] **Step 2: 运行目标测试并确认 RED**

Run:

```bash
cd backend
uv run pytest tests/test_pixelflow_video_router.py -k "long_scene_video_prompt_reaches_skill" -v
```

Expected: 两条测试均 FAIL；v2 路由由 `_validate_scene_video_request()` 拦截，R1
统一工作流由 `_generation_requests()` 拦截。

- [ ] **Step 3: 删除字符硬限制**

从 `_validate_scene_video_request()` 删除：

```python
if len(prompt) > 2500:
    raise SceneVideoCapabilityError(...)
```

如果 `prompt` 参数不再用于该函数，则从签名和调用点移除，避免保留误导性参数；同时
删除 `agent_workflows/video/video_generation.py` 的同类长度判断，其他能力校验保持
不变。

- [ ] **Step 4: 同步接口调用合同**

修改 `CONTENT_APP_API_CALLS.md` 的 `/api/video/reference-mode-video` 条目：

- 删除 PixelFlow 校验 2500 字提示词的描述。
- 明确 PixelFlow 不设置本地字符硬上限。
- 明确 content-app 或供应商的真实提示词错误继续透传。

检索最新设计文档中的 `2500`，存在同类说明时同步更新。

- [ ] **Step 5: 运行目标测试并确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/test_pixelflow_video_router.py -k "long_scene_video_prompt_reaches_skill" -v
```

Expected: PASS，Fake Skill 收到完整超长提示词。

- [ ] **Step 6: 运行完整后端回归**

Run:

```bash
cd backend
uv run pytest tests/test_pixelflow_video_router.py tests/test_scene_packages.py -v
uv run ruff check app/gateway/routers/pixelflow_video.py pixelflow/generate/scene_packages.py tests/test_pixelflow_video_router.py
```

Expected: 全部通过，无 Ruff 错误。

- [ ] **Step 7: 提交第三组代码和文档**

```bash
git add backend/app/gateway/routers/pixelflow_video.py backend/tests/test_pixelflow_video_router.py CONTENT_APP_API_CALLS.md docs/pixelflow-agent-skill-flow-latest-design.md
git commit -m "修复：移除分镜提示词字符硬限制" -m "超过2500字符的完整分镜提示词继续进入视频生成 Skill，并同步更新 content-app 调用合同。"
```

### Task 4: 最终验证、同步远端并推送

**Files:**
- Verify only

**Interfaces:**
- Consumes: Tasks 1-3 的所有提交
- Produces: 已验证并推送的 `feature/agent_0.8.4_boguan`

- [ ] **Step 1: 执行差异与中文规范检查**

```bash
git diff --check origin/feature/agent_0.8.4_boguan...HEAD
git log --format="%s%n%b" origin/feature/agent_0.8.4_boguan..HEAD
```

如果环境提供 PowerShell，再执行：

```bash
pwsh scripts/agentization/Test-ChineseEngineeringPolicy.ps1 \
  -RepositoryPath "$PWD" \
  -BaseRef origin/feature/agent_0.8.4_boguan \
  -HeadRef HEAD
```

- [ ] **Step 2: 拉取远端并处理冲突**

```bash
git fetch origin
git rebase origin/feature/agent_0.8.4_boguan
```

若出现冲突，保留双方业务改动，重新运行 Task 3 的完整回归。

- [ ] **Step 3: 推送当前分支**

```bash
git push origin feature/agent_0.8.4_boguan
```

- [ ] **Step 4: 记录最终证据**

最终交付说明必须列出：

- 根因和修复方式。
- RED/GREEN 测试命令及结果。
- 完整后端回归和 Ruff 结果。
- 中文工程门禁是否实际执行；若本机缺少 PowerShell，明确说明未执行原因。
- 最终提交 SHA 和推送分支。
