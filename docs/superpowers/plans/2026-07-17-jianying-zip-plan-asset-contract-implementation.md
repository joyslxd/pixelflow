# 剪映 ZIP 结果与 Plan 场景资产合同 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让剪映草稿异步任务消费第三方单个 ZIP URL、原样上传 TOS，并阻止 Plan 中的时间/镜头/风格等元信息被场景包当作参考图素材生成。

**Architecture:** `HttpJianyingDraftSkill` 继续作为第三方 Client，只替换成功结果的下载归档阶段；Plan 资产校验放在 `scene_blueprint.py` 的纯领域函数中，Plan LLM 只定向修复资产数组，场景包执行前复用同一校验作为最后防线。Router、异步 job、对话幂等和前端状态合同保持不变。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、httpx、pytest、zipfile、DeepSeek Plan LLM、content-app `/api/upload`。

## Global Constraints

- 所有新增 Python 对外接口必须以 `/agent` 开头；本任务不新增路由。
- 第三方剪映成功结果只接受单个公开 HTTPS ZIP URL，不兼容旧 JSON URL 数组。
- ZIP 最大 200 MiB，流式下载，不解压、不重新压缩，校验非空 ZIP 后原样上传。
- Plan 每个分镜仍为 4-15 秒，总时长精确等于用户确认时长。
- Plan 是权威执行合同；场景包不得静默修改已审核 Plan。
- 代码注释、提交说明和用户可见错误使用中文。
- 实现后同步 `AGENTS.md`、`docs/pixelflow-agent-skill-flow-latest-design.md`、`CONTENT_APP_API_CALLS.md`。

---

### Task 1: 剪映 Provider 单 ZIP 结果合同

**Files:**
- Modify: `backend/tests/test_jianying_draft_http_skill.py`
- Modify: `backend/pixelflow/jianying_draft/http_skill.py`

**Interfaces:**
- Consumes: Provider 查询响应 `{"code": 200, "data": "https://.../draft.zip"}`。
- Produces: `HttpJianyingDraftSkill.generate(request) -> JianyingDraftResult`，成功结果仍返回自有 TOS `download_url`。

- [ ] **Step 1: 写入单 ZIP 成功合同失败测试**

把主成功测试改成 Provider 返回单个 ZIP URL，Mock 下载端返回真实 ZIP 字节；uploader 读取临时文件并断言字节与 Provider ZIP 完全一致。

```python
provider_zip = _zip_bytes({"draft_content.json": b'{"draft":"a"}'})

return httpx.Response(
    200,
    json={"code": 200, "message": "success", "data": "https://cdn.example.com/draft.zip"},
)

def uploader(path: str) -> dict[str, object]:
    assert Path(path).read_bytes() == provider_zip
    return {"success": True, "url": "https://tos.example.com/jianying/draft.zip"}
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_jianying_draft_http_skill.py::test_http_skill_creates_polls_downloads_and_uploads_provider_zip -q
```

Expected: FAIL，当前实现把字符串 `data` 判定为“结果为空”。

- [ ] **Step 3: 最小实现 ZIP 流式下载和原样上传**

在 `http_skill.py` 中：

- 删除 JSON 解析、源 JSON 命名、二次压缩和多文件数量配置。
- 增加 `_download_zip(source_url, destination)`，按 chunk 写临时文件并限制总字节数。
- 增加 `_validate_zip(path)`，使用 `zipfile.is_zipfile()` 和 `ZipFile.infolist()` 拒绝非 ZIP 和空 ZIP。
- `_download_and_upload()` 在线程安全的临时目录生命周期内调用 uploader。

目标接口：

```python
async def _download_and_upload(
    self,
    *,
    request: JianyingDraftRequest,
    provider_task_id: str,
    source_url: object,
) -> JianyingDraftResult: ...

async def _download_zip(self, source_url: object, destination: Path) -> None: ...
```

- [ ] **Step 4: 增加异常合同测试**

分别覆盖：旧数组、HTTP URL、非 ZIP、空 ZIP、超过 200 MiB 的 `Content-Length`、下载 404、下载 503 重试耗尽、上传失败。

- [ ] **Step 5: 运行剪映 HTTP Skill 测试并确认 GREEN**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_jianying_draft_http_skill.py -q
```

Expected: 全部 PASS。

### Task 2: 剪映第三方业务错误可见性

**Files:**
- Modify: `backend/tests/test_jianying_draft_http_skill.py`
- Modify: `backend/pixelflow/jianying_draft/http_skill.py`

**Interfaces:**
- Consumes: Provider 的 `code/message`。
- Produces: 长度受限且不含凭据的 `JianyingDraftResult.message`。

- [ ] **Step 1: 写入 40101 错误原因测试**

```python
assert result.status == JianyingDraftStatus.FAILED
assert result.message == "第三方剪映草稿任务创建失败：token 缺失或无效"
assert call_count == 1
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_jianying_draft_http_skill.py -k business_message -q
```

Expected: FAIL，当前只返回通用错误。

- [ ] **Step 3: 实现安全业务消息提取**

新增纯函数：

```python
def _public_business_message(prefix: str, body: dict[str, Any]) -> str:
    message = re.sub(r"[\r\n\t]+", " ", str(body.get("message") or "")).strip()
    message = message[:160]
    return f"{prefix}：{message}" if message else prefix
```

创建和查询非成功业务码均复用该函数；日志和响应不包含 token、Authorization、响应体或堆栈。

- [ ] **Step 4: 运行剪映 Skill 与 Service/Router 回归测试**

Run:

```bash
cd backend && .venv/bin/pytest \
  tests/test_jianying_draft_http_skill.py \
  tests/test_jianying_draft_service.py \
  tests/test_pixelflow_jianying_draft_router.py -q
```

Expected: 全部 PASS。

### Task 3: Plan 资产语义校验与定向修复

**Files:**
- Modify: `backend/tests/test_scene_blueprint_quality.py`
- Modify: `backend/tests/test_creative_plan_markdown.py`
- Modify: `backend/pixelflow/creative/scene_blueprint.py`
- Modify: `backend/pixelflow/creative/plan_llm.py`
- Modify: `backend/pixelflow/creative/plan_markdown.py`

**Interfaces:**
- Produces: `asset_requirement_quality_issues(blueprints) -> list[str]`。
- Produces: `validate_asset_requirement_quality(blueprints) -> None`。
- Produces: `apply_asset_requirement_repairs(blueprints, repairs, total_duration_sec) -> list[dict[str, Any]]`。
- Produces: `repair_plan_asset_requirements(...) -> dict[str, Any]`。

- [ ] **Step 1: 写入资产质量规则失败测试**

合法样例：周衡、林悦、G500头等舱、万米高空金色云海、蓝妹啤酒瓶、玻璃杯、开瓶器。

非法样例：三秒钩子、0-3秒、段A、穿透运镜、背景音乐、9:16竖屏、8K真人质感、@图片1、@视频3。

```python
issues = asset_requirement_quality_issues(blueprints)
assert any("三秒钩子" in issue for issue in issues)
assert any("@图片1" in issue for issue in issues)
```

- [ ] **Step 2: 运行规则测试并确认 RED**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_scene_blueprint_quality.py -k asset_requirement -q
```

Expected: FAIL，函数尚不存在。

- [ ] **Step 3: 实现纯领域校验函数**

在 `scene_blueprint.py` 中识别：

- 时间范围和时长表达；
- `@图片N/@视频N` 占位符；
- 段落/叙事职能；
- 摄影、声音、风格和规格元信息。

错误必须包含 `scene_index`、集合名和非法值。合法实体名称不能因出现在故事标题中而被删除。

- [ ] **Step 4: 写入定向修复不篡改其他字段测试**

```python
repaired = apply_asset_requirement_repairs(original, repairs, total_duration_sec=60)
assert repaired[0]["asset_requirements"] == expected_assets
for field in ("duration_sec", "storyline", "shot_description", "narration", "transition"):
    assert repaired[0][field] == original[0][field]
```

- [ ] **Step 5: 实现 LLM 定向修复 Skill**

`repair_plan_asset_requirements()` 的 Prompt 只允许返回：

```json
{"scene_blueprints":[{"scene_index":1,"asset_requirements":{"characters":[],"scenes":[],"props":[]}}]}
```

提示词明确用户 Seedance 内容中的时间段、镜头指令、声音、风格和参考编号不是资产；实际人物、物理地点和有形物件才是资产。

- [ ] **Step 6: 接入初次 Plan 和 Agent 修改**

在镜头描述质量校验之后运行资产校验：

- 初次 Plan 修复失败时进入现有有效 fallback，不发布污染蓝图。
- Agent 修改修复失败时返回现有 `_failed_revision_result()`，保留当前有效版本。
- 修复成功后重新 `normalize_scene_blueprints()` 并再次校验。

- [ ] **Step 7: 运行 Plan 测试并确认 GREEN**

Run:

```bash
cd backend && .venv/bin/pytest \
  tests/test_scene_blueprint_quality.py \
  tests/test_creative_plan_markdown.py -q
```

Expected: 全部 PASS。

### Task 4: 场景包执行前防线

**Files:**
- Modify: `backend/tests/test_pixelflow_video_router.py`
- Modify: `backend/pixelflow/generate/scene_packages.py`
- Modify: `backend/app/gateway/routers/pixelflow_video.py`（仅当现有异常包装不能公开具体原因时）

**Interfaces:**
- Consumes: 当前激活 Plan 的 `scene_blueprints`。
- Produces: 合法蓝图对应的 `global_assets`；非法历史蓝图返回可读错误且不触发生图。

- [ ] **Step 1: 写入非法历史 Plan 不生成素材测试**

构造 `props=["三秒钩子", "蓝妹啤酒瓶"]`，断言场景包准备返回失败，错误指出分镜和非法资产，且图片 Skill 未被调用。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_pixelflow_video_router.py -k invalid_plan_asset -q
```

Expected: FAIL，当前会为“三秒钩子”创建 prop。

- [ ] **Step 3: 在映射 `global_assets` 前复用领域校验**

`prepare_video_scene_packages_with_llm()` 和规则 fallback 都在 `_align_global_assets_to_blueprints()` 前校验；不做过滤和隐式修复。

- [ ] **Step 4: 运行场景包和视频 Router 回归测试**

Run:

```bash
cd backend && .venv/bin/pytest \
  tests/test_pixelflow_video_router.py \
  tests/test_scene_assets.py -q
```

Expected: 全部 PASS。

### Task 5: 文档、静态检查与真实联调

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Modify: `CONTENT_APP_API_CALLS.md`

**Interfaces:**
- Documents: Provider 单 ZIP 合同、TOS 原样上传、资产质量校验和外部 token 阻塞。

- [ ] **Step 1: 同步三份项目文档**

删除“多个 JSON 重新打 ZIP”的旧描述，改为“单 ZIP 下载、校验、原样上传”；记录 Plan 资产三类实体和执行前校验。

- [ ] **Step 2: 运行格式与目标测试**

Run:

```bash
cd backend && .venv/bin/ruff check \
  pixelflow/jianying_draft/http_skill.py \
  pixelflow/creative/scene_blueprint.py \
  pixelflow/creative/plan_llm.py \
  pixelflow/creative/plan_markdown.py \
  pixelflow/generate/scene_packages.py \
  tests/test_jianying_draft_http_skill.py \
  tests/test_scene_blueprint_quality.py

cd backend && .venv/bin/pytest \
  tests/test_jianying_draft_http_skill.py \
  tests/test_jianying_draft_service.py \
  tests/test_pixelflow_jianying_draft_router.py \
  tests/test_scene_blueprint_quality.py \
  tests/test_creative_plan_markdown.py \
  tests/test_pixelflow_video_router.py \
  tests/test_scene_assets.py -q
```

Expected: ruff 无错误，目标测试全部 PASS。

- [ ] **Step 3: 使用 dev 配置真实调用第三方**

使用本机 `PIXELFLOW_CONFIG_ENV=dev` 和一个真实分镜视频：创建 Provider 任务、轮询到终态。成功时继续下载 ZIP 并通过真实 content-app `/api/upload` 上传 TOS；若仍为 `40101`，保留脱敏请求证据并明确标为外部凭据阻塞。

- [ ] **Step 4: 使用用户 Seedance 修改意见做真实 Plan 回归**

运行 Agent 修改，检查新版本 `scene_blueprints[].asset_requirements`；确认只有人物、物理场景和有形道具，没有钩子、时间、运镜、声音、风格或参考编号。若测试环境 LLM 超时，保留自动化定向修复测试结果并记录外部模型阻塞。

- [ ] **Step 5: 拉取远端、解决冲突并提交**

```bash
git fetch origin
git rev-list --left-right --count HEAD...origin/feature/dev_0.8.3_boguan
git add <本任务文件>
git commit -m "修复：适配剪映ZIP结果并校验场景资产"
```

Expected: 工作区只包含本任务修改，提交说明为中文。
