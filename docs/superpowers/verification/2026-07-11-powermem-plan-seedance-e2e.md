# 2026-07-11 PowerMem、Plan 回退与 Seedance 全系列真实验证

## 验证范围

- 当前分支：`feature/dev_0.8.2_boguan`
- 本地前端：test 模式，端口 5273
- 本地网关：dev profile，端口 8001
- 第三方调用：真实 content-app 与真实 PowerMem 测试环境
- 安全约束：本文不记录 Authorization、PowerMem key、产物 URL、供应商完整请求体或原始异常堆栈。

## PowerMem

### 真实服务探针

1. 使用独立测试用户并发执行 1 次 `record(infer=false)`、4 次 `search`、4 次 `health`。
2. 结果：写入成功，全部公开调用均返回类型正确的 fail-open 结果，无未捕获异常，`OB_SESSION_ENTRY_EXIST=0`。
3. 该高并发探针出现 8 次同步读总预算超时 warning：后台 record 占用共享请求闸门时，search/health 在 3 秒预算内 fail-open；主流程未阻断，warning 中没有 OB 错误。
4. 随后顺序执行真实 `health + search`：健康响应正常，检索到 1 条刚写入的测试记忆，warning 为 0，`OB_SESSION_ENTRY_EXIST=0`。
5. 图片、视频完整 E2E 期间再次检查网关日志，未发现 `OB_SESSION_ENTRY_EXIST`；可见 PowerMem warning 均为短预算 `TimeoutError`，主流程继续运行。

### 根因与修复结论

- 旧问题会在同一 `httpx.AsyncClient` 上并发进入 PowerMem/OceanBase 会话时复现；服务端临时会话状态冲突最终表现为 `OB_SESSION_ENTRY_EXIST`。
- 当前修复通过单服务请求闸门串行化共享客户端访问；锁等待与 HTTP 请求共享公开调用总预算，search/health 超时后按配置 fail-open。
- OB 定向重试只用于幂等 GET/search 的 5xx 响应；record 不做可能重复写入的 OB 自动重试。
- `aclose()` 会拒绝新请求、等待活动请求并回收后台任务，日志不会输出密钥或完整敏感响应。

## 图片完整流程

1. 需求：蓝色防泼水通勤背包，1 张 9:16 电商宣传图，真实摄影风，突出防泼水和大容量。
2. 完整执行：采集意图 → 图片表单 → 3 个创意方向 → 选择方向 → 生成 Plan v1。
3. 提交修改意见后生成 v2；服务端状态为 `planVersion=2`、历史 `[1,2]`。
4. 点击回退 v1：服务端变为 `planVersion=1`、历史仍为 `[1,2]`，没有产生 v3。
5. 刷新并恢复同一历史对话：仍为 v1，历史仍为 `[1,2]`。
6. 从回退后的 v1 提交新意见：此时才生成 v3，历史变为 `[1,2,3]`。
7. 同意 v3 后真实执行 `image/prepare` 与可恢复 `image/generate/start`；content-app 返回 1 张图片。
8. 浏览器确认图片元素实际加载完成；点击“满意，结束”后 `image_accepted=true`，pending image job 已清空。

## 视频完整流程

1. 需求：蓝色防泼水通勤背包，4 秒、9:16、真实 UGC 摄影风，直接转化。
2. 视频表单手动选择 `seedance-1.5`，用于验证 Seedance Prompt Skill 不受 2.0 型号限制；图片模型使用实时配置中的默认模型。
3. 完整执行：采集意图 → 视频表单 → 3 个创意方向 → 选择方向 → 生成 Plan v1。
4. 提交修改意见后生成 v2；服务端历史为 `[1,2]`，生产合同保持 `seedance-1.5 / 4 秒 / 9:16`。
5. 点击回退 v1：当前版本变为 1，历史仍为 `[1,2]`；刷新恢复后结果不变。
6. 从 v1 提交新意见后才生成 v3；历史为 `[1,2,3]`，合同仍为 `seedance-1.5 / 4 秒 / 9:16`，精确分镜时长为 `[4]`。
7. 真实场景包 job 生成 1 个分镜、1 个角色、1 个场景、1 个道具和 3 个资产 URL；场景资产失败数为 0，额度未暂停。
8. 浏览器分镜面板中的 36 个图片元素全部加载；镜头描述以 `0-4秒` 和十进制秒时间点表达，不包含 `ms/毫秒/毫秒时间码`，引用素材数为 3。

### E2E 中发现并修复的端点能力问题

- 初次分镜生成时，旧路由只要看到参考图片就调用 r2v；content-app 明确返回 `task_type=r2v` 不支持当前 `seedance-1.5` 映射模型，3 次尝试均失败。
- 实时 `/api/modelParamConfig/listByCategory/video_generate` 表明该模型只有“首尾帧、文生视频”，没有“全能参考”。
- 修复后，前端把所选模型的 `modelGenerateTypeList/uploadFileTypeList` 固化为 `video_model_capabilities`；后端完全按该实时能力路由，不按模型名称猜能力。
- 自动场景只有在“全能参考”可用时走 r2v；否则只在“文生视频”可用时走 t2v。角色/场景/道具资产不能冒充首尾帧。
- 显式首帧、首尾帧、参考、编辑、延伸必须同时满足对应能力与素材；不兼容时返回一次性能力错误，不静默改写用户操作语义。
- 旧对话没有能力快照时保留 legacy 首次选择；供应商明确拒绝 `task_type` 后，只改试一次 t2v，不再重复同一无效 r2v。
- 使用原失败分镜重试时，日志确认只发生 1 次旧 r2v 拒绝，随后 t2v 成功；最终 `sceneVideoCount=1`、失败数为 0、实际 mode 为 `text_to_video`。

### 合并与最终确认

- 只有 1 个分镜，merge job 按设计直接复用该分镜视频，不调用 content-app 多视频合并接口。
- 最终视频浏览器元素 `readyState=4`，实际时长约 4.05 秒，无播放错误。
- 点击“无意见，结束”后 `video_accepted=true`，pending video job 已清空。

## Skill 与第三方声明

- `seedance-prompt/SKILL.md` 对所有 Seedance 系列模型通用，实际型号通过运行时合同传入；2.0 只保留为表单推荐默认值。
- `THIRD_PARTY_NOTICE.md` 保留。它记录上传 ZIP 与第三方 Skill 两个输入来源、哈希、授权与改写边界，属于有价值的来源审计文件，不应删除。

## 自动化验证

- 后端相关矩阵：178 passed，Ruff 通过；仅有现存 LangChain pending deprecation warning。
- 前端：主流程契约 51 passed、active Plan 4 passed、Plan 消息恢复 9 passed、视频模型能力配置 5 passed；TypeScript lint 与 test build 均通过。
- 最终代码复审覆盖：PowerMem 生命周期与 OB 重试边界、Plan 回退/断线恢复、Seedance Skill、实时视频能力端点路由和旧合同恢复。
- 曾尝试仓库完整后端套件：`3839 passed, 42 skipped, 110 failed, 37 errors`。失败/错误集中在已有环境与顺序依赖（认证缓存命名、Kubernetes、Windows sandbox/symlink、迁移环境等），不作为本次变更通过依据；本次涉及模块的聚焦矩阵全部通过。
