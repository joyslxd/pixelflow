# Plan 与视频场景资产严格一致设计

## 1. 背景与目标

当前视频 Plan 阶段只在 `scene_blueprints[].asset_requirements` 中保存人物、场景、道具名称数组。场景包阶段再次调用 LLM 补充人物说明、三视图提示词、场景图提示词和道具图提示词。两个阶段分别生成语义，导致最终场景包中的资产名称、数量或视觉内容可能与用户同意的 `plan.md` 不一致。

本次改造建立一个 Plan 级权威资产清单 `asset_manifest`。它与 `scene_blueprints`、`creation_contract` 一起组成最终生产合同。用户同意 Plan 后，场景包、图片生成和前端 `@` 引用只能消费该版本合同，不得重新命名、增删或改写资产。

成功标准：

- `plan.md` 必须展示完整的出场角色、道具和场景列表及其文字说明和实际生图要求。
- 初次生成、反馈修订、手工编辑发布和历史版本恢复都保存各自版本的 `asset_manifest`。
- 分镜资产需求的去重并集与 `asset_manifest` 三类清单完全相等。
- 同一资产跨多个分镜只保留一个全局清单项，只生成一张素材图，并由所有相关分镜复用。
- 同一角色的不同服装、妆发或造型版本必须使用不同名称，作为不同资产项分别生成。
- 场景包中的资产名称、前端 `@` 候选名称和 `mentions.name` 必须逐字等于最终 Plan 清单的 `name`。
- 每个 Plan 清单项恰好对应一个场景包全局资产记录和一次成功的图片生成结果；失败时保留原资产记录并返回可重试失败项，不伪造成功。

## 2. 方案选择

采用“Plan 级结构化权威资产清单”方案，不从 Markdown 反向解析，也不在场景包阶段重新分析资产。

未采用方案：

- 从 `plan.md` 文本反向解析：Markdown 标题或排版变化会造成漏项，无法作为稳定 DTO。
- 继续汇总 `scene_blueprints[].asset_requirements` 后由场景包 LLM 补描述：只能保证名称大致一致，无法保证用户审核的说明与实际生图 Prompt 一致。

## 3. 权威数据合同

视频 `PlanMarkdownResult`、Plan API 响应、Plan 历史版本和场景包请求新增 `asset_manifest`：

```json
{
  "characters": [
    {
      "asset_id": "character-lin-xiao",
      "name": "林晓",
      "description": "24岁女性通勤者，齐肩黑发，浅灰风衣，气质沉稳。",
      "three_view_prompt": "林晓人物三视图……同一个人物的正面、侧面、背面……"
    }
  ],
  "props": [
    {
      "asset_id": "prop-waterproof-backpack",
      "name": "黑色防水通勤背包",
      "description": "哑光黑色方形通勤背包，银色拉链，正面无文字。",
      "image_prompt": "黑色防水通勤背包产品参考图……"
    }
  ],
  "scenes": [
    {
      "asset_id": "scene-rainy-bus-stop",
      "name": "雨夜公交站",
      "description": "现代城市公交站，夜雨，冷蓝路灯，湿润地面反光。",
      "image_prompt": "雨夜公交站环境参考图……"
    }
  ]
}
```

合同规则：

- LLM负责根据用户明确要求、选中创意、全部分镜故事和镜头内容生成清单名称、说明及生图 Prompt。
- 后端不信任 LLM 返回的 `asset_id`，按类型和规范化名称生成稳定 ID；同一版本内 ID 全局唯一。
- `name` 是唯一业务展示名称。`global_assets[].name`、`shot_description.mentions[].name` 和前端 `@` 选择器必须原样复用。
- 三类清单内以及跨三类清单的名称均不得重复，避免文本替换和 `@` 选择产生歧义。
- `characters` 必须提供非空 `description` 和 `three_view_prompt`；`props`、`scenes` 必须提供非空 `description` 和 `image_prompt`。
- `characters` 只能包含人物；商品、包装和工具归入 `props`；物理环境归入 `scenes`。
- `visual_style` 继续由 `creation_contract` 管理，不计入图片资产数量。

## 4. Plan 生成与修订

### 4.1 初次生成

Plan LLM一次返回：

- `plan_markdown`
- `scene_blueprints`
- `asset_manifest`
- 场景图片规格

LLM Prompt 明确要求先综合用户需求和全部分镜识别唯一资产，再为每个资产生成可审核文字说明和实际生图 Prompt。用户明确点名的角色、道具或场景必须进入相应分镜并出现在全局清单中，不能只出现在清单而不出场。

后端依次执行：

1. 规范化分镜时间线、故事结构和镜头描述。
2. 规范化 `asset_manifest` 并生成稳定 `asset_id`。
3. 校验每个分镜的 `asset_requirements` 都能在清单同分类中精确找到。
4. 校验清单中的每项至少被一个分镜引用。
5. 校验三类资产的名称集合与分镜需求去重并集完全相等。
6. 校验说明和生图 Prompt 完整、人物/商品/场景分类合法。

如果资产合同不合法，调用一次专用 LLM 修正。修正接口只能返回完整 `asset_manifest` 和逐分镜 `asset_requirements`，不得修改故事线、镜头描述、旁白、转场、时长或创作合同。初次 Plan 修正仍失败时使用现有确定性 Plan 兜底，同时由兜底蓝图生成完全一致的兜底清单并标记 `llm_used=false`。

### 4.2 Markdown 权威渲染

后端不直接信任 LLM在 Markdown 中书写的资产章节，而是用已校验的 `asset_manifest` 固定渲染并替换第四章：

```markdown
## 四、全局资产清单

### 4.1 出场角色列表
- 名称：林晓
  - 文字说明：……
  - 三视图生成要求：……

### 4.2 道具列表
- 名称：黑色防水通勤背包
  - 文字说明：……
  - 图片生成要求：……

### 4.3 场景列表
- 名称：雨夜公交站
  - 文字说明：……
  - 图片生成要求：……
```

空分类明确显示“无”，不能保留示例人物或占位符。第五章镜头列表仍由结构化蓝图固定渲染，因此用户审核的 Markdown 与后端生产 DTO 来自同一份结构化数据。

### 4.3 修订、手工编辑和恢复

- 用户反馈修订：Plan LLM必须返回完整的新 `scene_blueprints` 和 `asset_manifest`。未修改的同名资产可保留原说明；新增、删除、重命名或改变造型时必须同步更新清单和所有分镜引用。
- 手工编辑发布：现有手工编辑入口继续通过 Plan 修订 LLM 把 Markdown 差异转换为结构化合同；只有新蓝图和新清单全部校验通过才发布下一版本。
- 修订校验失败：不得发布新版本，保留当前激活版本的蓝图、清单和 Markdown。
- 历史恢复：直接恢复历史版本自己的 `asset_manifest`，不得继承当前版本或其他版本清单。
- 历史 Plan 缺少 `asset_manifest`：禁止继续生成场景包，返回“该方案缺少权威资产清单，请重新生成或修订 plan.md 后继续”，不能在用户同意后偷偷补写资产。

Plan 历史快照必须深拷贝 `asset_manifest`，保证后续修改不会污染旧版本。

## 5. 场景包生成

前端同意 Plan 时提交当前激活版本的：

- `plan_markdown`
- `scene_blueprints`
- `asset_manifest`
- `creation_contract`
- 表单、创意方向和附件

主流程存在 `scene_blueprints` 和 `asset_manifest` 时，不再调用场景包 LLM生成全局资产。场景包 Service 做确定性 DTO 转换：

1. 将 `asset_manifest.characters/scenes/props` 原序复制为 `global_assets`。
2. 只附加空的 `three_view_images` 或 `images`，不改名称、说明和生图 Prompt。
3. 根据每个分镜的 `asset_requirements` 精确解析对应 `asset_id`。
4. `reference_asset_ids` 顺序按人物、场景、道具以及各分类在分镜中的声明顺序生成。
5. `shot_description.text` 只把资产名称绑定为 `@asset_id`，不改故事、镜头、旁白或声音内容。
6. `shot_description.mentions[].name` 从 `asset_manifest.name` 复制，不接受其他别名。
7. 每个分镜最多9个唯一图片引用，超过时在生成图片前失败。

生成前后都执行清单一致性校验。`global_assets` 三类资产的 ID、名称、说明和 Prompt 必须与最终 Plan 清单逐项相等；不能出现额外默认主讲人、默认目标用户、默认商品或默认场景。

## 6. 图片生成与失败处理

每个清单项建立且只建立一个图片任务：

- 人物：使用 Plan 中的 `three_view_prompt` 调用文生图，生成一张包含同一人物正面、侧面、背面的三视图合成图。
- 道具：使用 Plan 中的 `image_prompt`；有用户参考图时走参考生图，否则走文生图。
- 场景：使用 Plan 中的 `image_prompt`；有用户参考图时走参考生图，否则走文生图。

图片模型、比例和清晰度继续严格读取 `creation_contract.image_model`、`scene_image_ratio` 和 `scene_image_size`。供应商即使返回多张 URL，每个资产记录也只接收合同要求的一张；返回零张视为失败。

图片任务串行执行并保持清单顺序。额度不足时暂停，尚未执行的清单项进入可恢复失败合同；普通失败保留该资产记录和空图片数组，记录实际端点、Prompt、模型参数和尝试链。只要任一资产没有成功获得一张图，整体资产生成不能报告完全成功。

## 7. 前端名称与 `@` 引用

前端不自行生成展示名称：

- 资产卡片标题读取 `global_assets[].name`。
- `@` 选择器候选名称读取同一字段。
- 插入镜头文本的是稳定 `@asset_id`，可视 chip 展示权威 `name`。
- `mentions[].name` 必须与 `global_assets` 中同 ID 的名称完全一致。
- 用户替换或编辑资产图片时只能替换 URL，不得修改 `asset_id` 或 `name`。
- 删除资产仍按现有交互执行，但删除后场景包成为用户编辑态；再次生成视频前必须保证所有仍存在的分镜引用可解析，不能静默引用不存在资产。

前端 conversation context、Plan 消息恢复和 pending job 请求必须保存 `asset_manifest`，刷新、切换对话和历史恢复后不能丢失。

## 8. API 与兼容性

以下合同增加 `asset_manifest`：

- `POST /agent/flows/planning/plan` 响应
- `POST /agent/flows/planning/plan/revise` 请求与响应
- `POST /agent/flows/planning/plan/manual-edit` 请求与响应
- `POST /agent/flows/planning/plan/restore` 请求与响应
- Plan 历史版本条目
- `POST /agent/flows/video/prepare-scene-packages` 请求
- `POST /agent/flows/video/prepare-scene-packages/start` 请求及 pending job 持久化内容

新创建或新修订的视频 Plan 必须有非空对象形式的 `asset_manifest`；三个分类数组可以为空。图片 Plan 不使用该字段，保持空对象。

旧对话可以继续查看，但旧视频 Plan 在没有权威清单时不能直接进入场景包生成。前端展示可恢复提示，让用户修订或重新生成 Plan，不自动发起新计费任务。

## 9. 测试与真实流程验收

### 9.1 自动化测试

- Plan 初次生成：LLM返回两个人物、三个道具和两个场景时，Markdown 三个列表、`asset_manifest` 和分镜需求并集完全一致。
- Plan 资产修正：模拟 LLM漏项、多项、错分类、重名、空说明和空 Prompt，确认只修资产合同，不改故事、镜头、旁白或时长。
- Plan 修订：新增角色、删除道具、角色换装时，新版本清单和 Markdown 同步，旧历史版本不变。
- 手工编辑：用户修改清单名称或说明时，发布的新版本结构化清单与 Markdown 一致；校验失败不发布。
- 历史恢复：恢复版本同时恢复对应 `asset_manifest`。
- 场景包：模拟场景包旧 LLM返回额外资产，确认主流程不调用或不采纳它；输出数量、名称、说明和 Prompt 与 Plan 清单逐项相等。
- 引用：每个分镜只引用自身 `asset_requirements`，重复资产跨分镜复用同一 ID；`mentions.name` 与 Plan 名称逐字相同。
- 图片任务：每个唯一资产只调用一次图片 Skill，成功后每项恰好一个 URL；缺图、额外 URL、额度暂停和普通失败符合合同。
- 前端：API 类型、Plan 消息恢复、pending job 和 `@` 候选名称均保留 `asset_manifest` 和权威名称。

### 9.2 真实联调

使用一条明确包含多人物、多道具、多场景和重复跨镜资产的电商视频需求，真实执行：

1. 调用 Plan API，让实际 LLM生成 Plan、分镜蓝图和资产清单。
2. 检查 Markdown 三个列表与 `asset_manifest` 的名称、说明和 Prompt。
3. 模拟用户同意当前 Plan，启动真实场景包异步任务。
4. 使用用户临时提供的 Authorization 调用 content-app 图片接口；令牌只放进当前进程环境，不写文件、不打印。
5. 轮询至场景资产生成完成或返回明确可恢复失败。
6. 自动比较三类清单的数量、ID、名称、说明、Prompt、图片 URL 数量和每个分镜 mentions。
7. 下载全部成功图片并逐张查看，核对人物造型、道具外观、场景空间与最终 Plan 的文字说明。
8. 若发现合同或视觉不一致，先增加可复现失败测试，再修复并重跑自动化测试与真实流程，直至不存在已知不一致。

真实联调不会把 Authorization、完整供应商响应中的敏感字段或图片私有查询参数写入 PowerMem、测试快照、设计文档或日志。

## 10. 文档同步

实现完成后同步更新：

- `docs/pixelflow-agent-skill-flow-latest-design.md`
- `CONTENT_APP_API_CALLS.md`，说明场景资产调用数量由最终 Plan 唯一资产清单决定；接口路径不变
- 必要的前端 API 类型说明和测试夹具

本次不改变图片或视频供应商端点，不新增数据库表；权威清单随现有 Plan 消息、历史快照和 conversation context 持久化。
