/** Skill 脚本阶段与时间线步骤的展示映射（前端权威投影侧）。 */

export const SCRIPT_SKILL_STAGE_ORDER = [
  "start",
  "plan",
  "characters",
  "outline",
  "episode",
  "review",
  "compliance",
  "export",
] as const;

export type ScriptSkillStageId = (typeof SCRIPT_SKILL_STAGE_ORDER)[number];

export const SCRIPT_SKILL_STAGE_LABEL: Record<ScriptSkillStageId, string> = {
  start: "选题与创作目标",
  plan: "三幕结构与爽点",
  characters: "角色/场景/道具设定",
  outline: "分镜大纲",
  episode: "剧本正文",
  review: "五维自检",
  compliance: "合规检查",
  export: "导出终稿",
};

export function isScriptSkillStageId(value: string): value is ScriptSkillStageId {
  return (SCRIPT_SKILL_STAGE_ORDER as readonly string[]).includes(value);
}

/** 从步骤标题（如「五维自检 /review」）解析阶段 id。 */
export function stageIdFromStepTitle(title: string): ScriptSkillStageId | null {
  const match = title.match(/\/(start|plan|characters|outline|episode|review|compliance|export)\b/u);
  return match && isScriptSkillStageId(match[1]) ? match[1] : null;
}

/** 从 artifact:video-script-{stage}-{digest} 解析阶段 id。 */
export function stageIdFromArtifactRef(reference: string): ScriptSkillStageId | null {
  const match = reference.match(/^artifact:video-script-([a-z]+)-/u);
  return match && isScriptSkillStageId(match[1]) ? match[1] : null;
}

export function stageIdFromStep(step: {
  title: string;
  artifactRefs: readonly string[];
}): ScriptSkillStageId | null {
  const fromTitle = stageIdFromStepTitle(step.title);
  if (fromTitle) return fromTitle;
  for (const reference of step.artifactRefs) {
    const fromRef = stageIdFromArtifactRef(reference);
    if (fromRef) return fromRef;
  }
  return null;
}

export function shortStageLabel(stageId: ScriptSkillStageId | null, fallbackTitle: string): string {
  if (stageId) return SCRIPT_SKILL_STAGE_LABEL[stageId];
  return fallbackTitle.replace(/\s*\/\w+\s*$/u, "").trim() || fallbackTitle;
}

/** 从阶段 Markdown 抽 1～3 个小节标题，供时间线展示「改了什么」。 */
export function extractStageChangeHints(markdown: string, limit = 3): string[] {
  const hints: string[] = [];
  for (const line of markdown.split(/\r?\n/u)) {
    const trimmed = line.trim();
    const heading = trimmed.match(/^#{1,3}\s+(.+)$/u);
    if (!heading) continue;
    const text = heading[1].replace(/^\/\w+\s*/u, "").trim();
    if (!text || hints.includes(text)) continue;
    hints.push(text);
    if (hints.length >= limit) break;
  }
  return hints;
}

/** 用户明确确认脚本方案后，才允许进入成片/资产包。 */
export function isConfirmScriptPlanRequest(content: string): boolean {
  const text = content.trim().toLowerCase();
  if (!text) return false;
  const markers = [
    "确认脚本",
    "确认方案",
    "确认plan",
    "确认执行方案",
    "确认脚本方案",
    "确认脚本plan",
    "确认并生成视频",
    "确认并生成资产包",
    "同意脚本",
    "同意方案",
  ];
  return markers.some((marker) => text.includes(marker.toLowerCase()));
}

/** 用户明确要求按新需求重做任务规划。 */
export function isRedesignTaskPlanRequest(content: string): boolean {
  const text = content.trim().toLowerCase();
  if (!text) return false;
  const markers = [
    "重新设计任务规划",
    "重新规划任务",
    "重做任务规划",
    "重新设计执行规划",
    "重新规划",
    "按新需求重做",
    "需求变了重新规划",
  ];
  return markers.some((marker) => text.includes(marker.toLowerCase()));
}

/**
 * 中途需求大幅变更：已有脚本/计划时，用户又发来像新 brief 的长输入。
 * 短确认/改资产包/继续生成不算。
 */
export function isMajorRequirementChangeRequest(
  content: string,
  previousBrief: string | null | undefined,
): boolean {
  const text = content.trim();
  if (!text || text.length < 36) return false;
  if (isConfirmScriptPlanRequest(text)) return false;
  if (isContinueVideoGenerationRequest(text) && text.length < 80) return false;
  if (isRegenerateVideoAssetPackageRequest(text)) return false;
  if (isReviseVideoAssetPackageRequest(text)) return false;
  if (isRedesignTaskPlanRequest(text)) return false;
  if (isConfirmGenerateVideoFromPackagesRequest(text)) return false;
  const looksLikeBrief = /(?:秒|分钟|9:16|16:9|短视频|广告|脚本|角色|朋友|品牌|产品|啤酒|宣传)/u.test(text)
    && text.length >= 36;
  if (!looksLikeBrief) return false;
  const previous = (previousBrief || "").trim();
  if (!previous) return false;
  if (previous === text) return false;
  // 同一 brief 的轻微补充不算大变更
  if (previous.includes(text) || text.includes(previous.slice(0, Math.min(80, previous.length)))) {
    return text.length > previous.length * 1.6;
  }
  const prevTokens = new Set(previous.replace(/\s+/gu, "").slice(0, 120).split(""));
  const nextTokens = text.replace(/\s+/gu, "").slice(0, 120).split("");
  const overlap = nextTokens.filter((ch) => prevTokens.has(ch)).length;
  return overlap / Math.max(nextTokens.length, 1) < 0.55;
}

/** 从设定 Markdown 抽具体产品/道具名，避免 product_info 落成「核心产品」。 */
export function extractConcreteProductHint(markdown: string): string {
  const text = markdown || "";
  const propSection = text.match(
    /#{1,3}\s*[^\n]*(?:道具(?:与产品)?设定|道具设定)[^\n]*\n([\s\S]*?)(?=#{1,3}\s|$)/u,
  )?.[1] ?? "";
  const source = propSection || text;
  for (const match of source.matchAll(/^#{2,4}\s+(.+)$/gmu)) {
    let name = match[1].replace(/[*_`]/gu, "").trim();
    name = name.split(/[（(：:\-—|/]/u)[0]?.trim() || name;
    if (!name || name.length > 40) continue;
    if (/^(核心产品|产品|商品|主商品|道具|关键道具|产品道具)$/u.test(name)) {
      const bodyStart = match.index ?? 0;
      const body = source.slice(bodyStart, bodyStart + 240);
      const labeled = body.match(/(?:名称|品牌|产品名|商品名)\s*[:：]\s*([^\n，,。；;]{2,40})/u)?.[1]?.trim();
      if (labeled && !/^(核心产品|产品|商品)$/u.test(labeled)) return labeled.slice(0, 120);
      continue;
    }
    return name.slice(0, 120);
  }
  const line = text
    .split("\n")
    .map((item) => item.trim())
    .find((item) => /(?:产品|品牌|片名|商品|道具)\s*[:：]/u.test(item));
  if (line) {
    return line
      .replace(/^#+\s*/u, "")
      .replace(/^(?:核心产品|产品|商品|主商品|道具)\s*[:：\-—]\s*/u, "")
      .slice(0, 120);
  }
  return "";
}

/**
 * 脚本就绪后要求进入成片/资产包。
 * 刻意收窄：不含裸「生成视频」，避免「根据这个脚本生成视频」误跳过确认与脚本 Plan。
 */
export function isContinueVideoGenerationRequest(content: string): boolean {
  const text = content.trim().toLowerCase();
  if (!text) return false;
  if (isConfirmScriptPlanRequest(text)) return true;
  if (isRegenerateVideoAssetPackageRequest(text)) return true;
  const markers = [
    "继续生成视频",
    "继续做视频",
    "继续出片",
    "继续生成资产包",
    "继续准备资产包",
    "生成资产包",
    "准备资产包",
    "视频资产包",
    "生成场景包",
    "准备场景包",
    "继续生成场景包",
  ];
  return markers.some((marker) => text.includes(marker.toLowerCase()));
}

/** 明确要求重做视频资产包 / 场景包（可带修改意见）。 */
export function isRegenerateVideoAssetPackageRequest(content: string): boolean {
  const text = content.trim().toLowerCase();
  if (!text) return false;
  const markers = [
    "重新生成视频资产包",
    "重新生成资产包",
    "重新生成场景包",
    "重做视频资产包",
    "重做资产包",
    "重做场景包",
    "再生成一次资产包",
    "再出一版资产包",
    "再生成资产包",
    "刷新资产包",
    "重跑资产包",
  ];
  return markers.some((marker) => text.includes(marker.toLowerCase()));
}

/**
 * 资产包待确认阶段的自然语言修改：提到资产包/场景包/参考图，或对角色/道具/场景提出改动。
 * 长脚本正文不算修改指令。
 */
export function isReviseVideoAssetPackageRequest(content: string): boolean {
  const text = content.trim();
  if (!text || text.length > 240) return false;
  if (isRegenerateVideoAssetPackageRequest(text)) return true;
  if (isConfirmScriptPlanRequest(text)) return false;
  if (isConfirmGenerateVideoFromPackagesRequest(text)) return false;
  const hasPackageTarget = /资产包|场景包|参考图|角色三视图|分镜素材|全局资产/.test(text);
  const hasAssetTarget = /角色|道具|场景|三视图|分镜|人物|服装|发型|造型/.test(text);
  const hasAction = /重新生成|重做|再生成|修改|改成|换成|调整|补齐|增加|删掉|删除|去掉|换掉|不要/.test(text);
  return hasAction && (hasPackageTarget || hasAssetTarget);
}

/** 资产包已就绪后，确认生成成片（不是重做资产包）。 */
export function isConfirmGenerateVideoFromPackagesRequest(content: string): boolean {
  const text = content.trim().toLowerCase();
  if (!text) return false;
  if (isRegenerateVideoAssetPackageRequest(text)) return false;
  const markers = [
    "确认并生成视频",
    "确认生成视频",
    "开始生成成片",
    "生成成片",
    "确认场景包并生成",
  ];
  return markers.some((marker) => text.includes(marker.toLowerCase()));
}

export interface ScriptCharacterReadiness {
  expectedCount: number;
  profileCount: number;
  hasCharacterSection: boolean;
  multiPersonCue: boolean;
  ready: boolean;
  missingHints: string[];
}

export interface ScriptReadinessInput {
  scriptContent?: string | null;
  stages?: ReadonlyArray<{ stageId: string; content: string }>;
}

/** 导出终稿已完成：才允许展示「确认脚本并生成资产包」。 */
export function workspaceHasExportReady(input: ScriptReadinessInput): boolean {
  const stages = input.stages ?? [];
  if (stages.some((stage) => stage.stageId === "export" && stage.content.trim())) {
    return true;
  }
  // 无分阶段产物时，仅当终稿正文本身像导出件（含设定集 + 镜头）才放行
  const script = input.scriptContent?.trim() ?? "";
  if (!script) return false;
  const hasSettings = /角色设定|场景设定|道具/.test(script);
  const hasShots = /镜头|分镜|00:\d{2}/.test(script);
  return hasSettings && hasShots && script.length >= 200;
}

/** 合并终稿与设定阶段，供角色完备性检查（预览里有设定但终稿未粘贴时也能识别）。 */
export function buildScriptReadinessCorpus(input: ScriptReadinessInput): string {
  const parts: string[] = [];
  const stages = input.stages ?? [];
  for (const stageId of ["characters", "export", "episode"] as const) {
    const hit = stages.find((stage) => stage.stageId === stageId && stage.content.trim());
    if (hit) parts.push(hit.content.trim());
  }
  const script = input.scriptContent?.trim() ?? "";
  if (script) parts.push(script);
  // 去重保序
  return [...new Set(parts)].join("\n\n");
}

function extractCharacterSection(text: string): string {
  const patterns = [
    /#{1,3}\s*[0-9一二三四五六七八九十.、)）]*\s*角色设定[\s\S]*?(?=#{1,3}\s*[0-9一二三四五六七八九十.、)）]*\s*(?:场景设定|道具|大纲|完整镜头|合规)|$)/u,
    /#{1,3}\s*角色\s*[/／]\s*场景\s*[/／]\s*道具[^\n]*[\s\S]*?(?=#{1,3}\s*(?:大纲|完整镜头|合规|三幕)|$)/u,
    /#{1,3}\s*[^\n]*\/characters[^\n]*[\s\S]*?(?=#{1,3}\s*[^\n]*\/(?:outline|episode|review|compliance|export)\b|$)/iu,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match?.[0]?.trim()) return match[0];
  }
  return "";
}

function countCharacterProfiles(section: string): number {
  if (!section.trim()) return 0;
  const names = new Set<string>();
  const collect = (value: string | undefined) => {
    const name = (value || "").replace(/[*_#`]/gu, "").trim();
    if (!name || name.length > 24) return;
    if (/^(角色设定|场景设定|道具|视觉形象|身份|性格|金句|核心标签)/u.test(name)) return;
    names.add(name);
  };

  for (const match of section.matchAll(/^#{2,4}\s+(.+)$/gmu)) {
    collect(match[1].split(/[（(：:\-—|]/u)[0]);
  }
  for (const match of section.matchAll(/^[-*]\s+\*{0,2}([^:*\n]{1,24})\*{0,2}\s*[:：]/gmu)) {
    collect(match[1]);
  }
  for (const match of section.matchAll(/\*\*([^*]{1,24})\*\*/gu)) {
    collect(match[1].split(/[（(：:\-—|]/u)[0]);
  }
  for (const match of section.matchAll(
    /(?:主角|配角|人物|角色|男主|女主|男\s*[1234一二三四]|女\s*[1234一二三四])[：:\s]*([^\s，,；;（(/]{1,12})/gu,
  )) {
    collect(match[1]);
  }
  // 「阿杰（男1）」行内声明
  for (const match of section.matchAll(
    /([\u4e00-\u9fffA-Za-z]{1,12})\s*[（(]\s*(?:男|女)\s*[1234一二三四]/gu,
  )) {
    collect(match[1]);
  }
  return names.size;
}

/** 从脚本/阶段产物启发式判断角色设定是否够清晰。 */
export function analyzeScriptCharacterReadiness(
  markdownOrInput: string | ScriptReadinessInput,
): ScriptCharacterReadiness {
  const input: ScriptReadinessInput = typeof markdownOrInput === "string"
    ? { scriptContent: markdownOrInput }
    : markdownOrInput;
  const text = buildScriptReadinessCorpus(input).trim();
  const missingHints: string[] = [];
  if (!text) {
    return {
      expectedCount: 0,
      profileCount: 0,
      hasCharacterSection: false,
      multiPersonCue: false,
      ready: false,
      missingHints: ["脚本为空，请先生成或粘贴完整脚本"],
    };
  }

  const charactersStage = (input.stages ?? []).find(
    (stage) => stage.stageId === "characters" && stage.content.trim(),
  );
  const section = extractCharacterSection(text)
    || (charactersStage ? charactersStage.content : "");
  const hasCharacterSection = Boolean(section.trim())
    || /角色设定|角色\s*[/／]\s*场景|\/characters\b/iu.test(text);
  const hasSceneSettings = /场景设定|\/characters\b/iu.test(text);
  const hasPropSettings = /道具(?:与产品)?设定|道具设定/u.test(text);

  // 期望人数：优先在设定章节内统计，避免镜头正文「男1/女1」虚高
  const roleLabelSource = section || text;
  const roleLabels = new Set(
    [...roleLabelSource.matchAll(/(?:男|女)\s*[1234一二三四]/gu)]
      .map((match) => match[0].replace(/\s+/gu, "")),
  );
  let expectedCount = roleLabels.size;
  if (expectedCount < 2) {
    const cueSource = section || text;
    if (/四个朋友|四位朋友|四人组|四位老友|四人聚会/u.test(cueSource)) expectedCount = 4;
    else if (/三个朋友|三位朋友|三人组/u.test(cueSource)) expectedCount = 3;
    else if (/两位朋友|两个朋友|二人组/u.test(cueSource)) expectedCount = 2;
  }
  const multiPersonCue = expectedCount >= 2
    || /多人|群戏|好友们|朋友们|同学聚会|老友局/u.test(section || text);

  const profileCount = countCharacterProfiles(section);

  // 流水线已产出 characters 阶段，且含场景/道具块：视为设定齐全（信任阶段产物）
  if (charactersStage && hasCharacterSection && hasSceneSettings && hasPropSettings) {
    if (profileCount >= 1 || /视觉形象|身份|核心标签/u.test(charactersStage.content)) {
      return {
        expectedCount: Math.max(expectedCount, profileCount),
        profileCount: Math.max(profileCount, 1),
        hasCharacterSection: true,
        multiPersonCue,
        ready: true,
        missingHints: [],
      };
    }
  }

  if (multiPersonCue && !hasCharacterSection) {
    missingHints.push("缺少「角色设定」章节，需补齐每位出镜人物的视觉形象与身份");
  }
  if (multiPersonCue && expectedCount >= 2 && profileCount > 0 && profileCount < expectedCount) {
    missingHints.push(
      `剧本像是 ${expectedCount} 人戏，但角色设定仅识别到 ${profileCount} 人，请补充全部角色`,
    );
  }
  if (multiPersonCue && profileCount < 2 && !charactersStage) {
    missingHints.push("多人出镜时至少需要 2 个可区分的角色设定，否则资产包容易塌成单人");
  }

  return {
    expectedCount,
    profileCount,
    hasCharacterSection,
    multiPersonCue,
    ready: missingHints.length === 0,
    missingHints,
  };
}

export function scriptNeedsFullCharacterPlan(
  markdownOrInput: string | ScriptReadinessInput,
): boolean {
  const readiness = analyzeScriptCharacterReadiness(markdownOrInput);
  return readiness.multiPersonCue && !readiness.ready;
}

export function resolveGeneratableScriptMarkdown(input: {
  scriptContent?: string | null;
  stages?: ReadonlyArray<{ stageId: string; content: string }>;
}): string {
  const fromScript = input.scriptContent?.trim() ?? "";
  if (fromScript) return fromScript;
  const stages = input.stages ?? [];
  for (const stageId of ["export", "episode", "outline"] as const) {
    const hit = stages.find((stage) => stage.stageId === stageId && stage.content.trim());
    if (hit) return hit.content.trim();
  }
  const last = [...stages].reverse().find((stage) => stage.content.trim());
  return last?.content.trim() ?? "";
}

/**
 * 资产包专用正文：终稿 + 设定集阶段，避免只传 episode/export 时丢掉角色/道具。
 */
export function buildAssetPackagePlanMarkdown(input: ScriptReadinessInput): string {
  const base = resolveGeneratableScriptMarkdown(input);
  const stages = input.stages ?? [];
  const characters = stages.find((stage) => stage.stageId === "characters")?.content?.trim() ?? "";
  const exportStage = stages.find((stage) => stage.stageId === "export")?.content?.trim() ?? "";
  const primary = exportStage || base;
  const needsSettings = !/#{1,3}\s*[^\n]*角色设定/u.test(primary)
    || !/#{1,3}\s*[^\n]*场景设定/u.test(primary)
    || !/#{1,3}\s*[^\n]*道具/u.test(primary);
  if (characters && needsSettings) {
    return `${characters}\n\n---\n\n${primary}`.trim();
  }
  if (characters && primary && !primary.includes(characters.slice(0, Math.min(60, characters.length)))) {
    // 终稿有设定标题但可能很薄：仍附上 characters 阶段全文，便于后端确定性抽取
    if ((primary.match(/#{1,3}\s*[^\n]*角色设定/gu) || []).length > 0) {
      return `${characters}\n\n---\n\n${primary}`.trim();
    }
  }
  return primary || base || characters;
}

export function workspaceHasGeneratableScript(input: {
  scriptContent?: string | null;
  stages?: ReadonlyArray<{ stageId: string; content: string }>;
}): boolean {
  return resolveGeneratableScriptMarkdown(input).length > 0;
}
