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

/** Path A：/start 后的选题创意确认闸门（以公开步骤标题识别）。 */
export function isScriptCreativeConfirmationTitle(title: string | null | undefined): boolean {
  const text = (title || "").trim();
  return text.includes("确认选题创意");
}

/** 确认卡摘要仍缺画幅/结尾引导时，禁止直接「同意创意继续」。 */
export function creativeConfirmNeedsClarification(
  costSummary: string | null | undefined,
): boolean {
  return (costSummary || "").includes("还需要你确认");
}

/** 用户同意当前选题创意，继续后续 Skill 阶段。 */
export function isAgreeScriptCreativeRequest(content: string): boolean {
  const text = content.trim().toLowerCase();
  if (!text || text.length > 48) return false;
  if (isCancelScriptCreativeRequest(text)) return false;
  const exact = new Set([
    "同意",
    "可以",
    "确认",
    "没问题",
    "就这个",
    "就按这个",
    "继续",
    "接着做",
    "接着来",
    "好",
    "好的",
    "行",
    "ok",
    "okay",
    "yes",
    "同意创作",
    "同意创意",
  ]);
  if (exact.has(text)) return true;
  const markers = [
    "同意创作",
    "同意创意",
    "确认创意",
    "创意可以",
    "同意这个",
    "就这个创意",
    "可以继续",
    "好的继续",
    "按这个来",
    "就按这个方向",
  ];
  return markers.some((marker) => text.includes(marker.toLowerCase()));
}

/** 仅取消当前创意闸门，不立刻开新 Turn（等待用户补充方向）。 */
export function isCancelScriptCreativeRequest(content: string): boolean {
  const text = content.trim().toLowerCase();
  if (!text || text.length > 24) return false;
  const markers = ["换个方向", "取消", "不要这个", "不行", "重来", "重新想"];
  return markers.some((marker) => text === marker.toLowerCase());
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
  // 无分阶段产物时：终稿含设定集 + 镜头/时间码，即视为可确认（兼容 Path B 导入稿）。
  const script = input.scriptContent?.trim() ?? "";
  if (!script || script.length < 200) return false;
  const hasSettings = /角色设定|场景设定|道具/.test(script);
  const hasShots = /镜头|分镜|00:\d{2}|\d+\s*[—\-–~～到至]\s*\d+\s*秒/.test(script);
  if (hasSettings && hasShots) return true;
  // Path B：成熟导入稿常无独立设定章，但有连续分镜时间码即可确认。
  return hasShots && script.length >= 400;
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
        if (/^(角色设定|场景设定|道具|视觉形象|身份|性格|金句|核心标签|角色关系|角色档案)/u.test(name)) return;
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
 * 资产包专用正文：脚本预览分阶段产物（characters + outline）+ 终稿。
 * 避免只传 episode/export 时丢掉角色/道具/分镜提示词。
 */
export function buildAssetPackagePlanMarkdown(input: ScriptReadinessInput): string {
  const base = resolveGeneratableScriptMarkdown(input);
  const stages = input.stages ?? [];
  const characters = stages.find((stage) => stage.stageId === "characters")?.content?.trim() ?? "";
  const outline = stages.find((stage) => stage.stageId === "outline")?.content?.trim() ?? "";
  const exportStage = stages.find((stage) => stage.stageId === "export")?.content?.trim() ?? "";
  const episode = stages.find((stage) => stage.stageId === "episode")?.content?.trim() ?? "";
  const primary = exportStage || episode || base;
  const needsSettings = !/#{1,3}\s*[^\n]*角色设定/u.test(primary)
    || !/#{1,3}\s*[^\n]*场景设定/u.test(primary)
    || !/#{1,3}\s*[^\n]*道具/u.test(primary);
  const hasShotSection = /#{1,3}\s*[^\n]*(?:分镜提示词|镜头列表|分镜大纲)/u.test(primary);
  const parts: string[] = [];
  if (
    characters
    && (
      needsSettings
      || (primary && !primary.includes(characters.slice(0, Math.min(60, characters.length))))
    )
  ) {
    parts.push(characters);
  }
  if (
    outline
    && (
      !hasShotSection
      || (primary && !primary.includes(outline.slice(0, Math.min(60, outline.length))))
    )
  ) {
    parts.push(outline);
  }
  if (primary) parts.push(primary);
  if (parts.length > 0) return parts.join("\n\n---\n\n").trim();
  return primary || base || characters || outline;
}

export function workspaceHasGeneratableScript(input: {
  scriptContent?: string | null;
  stages?: ReadonlyArray<{ stageId: string; content: string }>;
}): boolean {
  return resolveGeneratableScriptMarkdown(input).length > 0;
}

/** 单步「导入成熟脚本」计划：仍展示执行方案卡（V2 不再静默隐藏）。 */
export function isSilentImportScriptPlan(_plan: {
  publicGoal?: string | null;
  steps: Record<string, { title: string; sequence?: number }>;
}): boolean {
  return false;
}

/** 补生产字段跟进：仍展示执行方案卡（V2 不再静默隐藏）。 */
export function isSilentProductionFieldsPlan(_plan: {
  publicGoal?: string | null;
  steps: Record<string, { title: string; sequence?: number }>;
}): boolean {
  return false;
}

/** @deprecated 静默导入已取消；保留函数签名供旧测试过渡。 */
export function buildSilentImportScriptNotice(
  publicSummary: string | null | undefined,
): string {
  const summary = (publicSummary || "").trim();
  const head = summary || "已导入脚本。";
  const missingMatch = head.match(/仍缺少：(.+?)(?:。|$)/);
  if (missingMatch) {
    const items = missingMatch[1].trim().replace(/。$/, "");
    return [
      head.endsWith("。") ? head : `${head}`,
      `请直接在对话框回复上述缺失项（${items}），我再继续。`,
    ].join("\n");
  }
  return [
    head.endsWith("。") ? head : `${head}。`,
    "如需继续，直接告诉我下一步即可。",
  ].join("\n");
}

/** 对话框里可点开右侧脚本预览的文案锚点。 */
export const SCRIPT_VERSION_PREVIEW_LINK_RE =
  /已(?:更新|导入)脚本版本\s*\d+|在右侧查看脚本/g;

export type ScriptVersionPreviewPart =
  | { kind: "text"; text: string }
  | { kind: "scriptVersion"; text: string };

export function splitScriptVersionPreviewParts(content: string): ScriptVersionPreviewPart[] {
  const text = content || "";
  if (!text) return [];
  const parts: ScriptVersionPreviewPart[] = [];
  let last = 0;
  for (const match of text.matchAll(SCRIPT_VERSION_PREVIEW_LINK_RE)) {
    const index = match.index ?? 0;
    if (index > last) {
      parts.push({ kind: "text", text: text.slice(last, index) });
    }
    parts.push({ kind: "scriptVersion", text: match[0] });
    last = index + match[0].length;
  }
  if (last < text.length) {
    parts.push({ kind: "text", text: text.slice(last) });
  }
  return parts.length > 0 ? parts : [{ kind: "text", text }];
}
