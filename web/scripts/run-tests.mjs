import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const contractOnly = process.argv.includes("--contracts");
const temporaryRoot = mkdtempSync(path.join(os.tmpdir(), "pixelflow-web-tests-"));
const typeTestRoot = mkdtempSync(path.join(webRoot, ".pixelflow-contract-types-"));
const hookTestRoot = mkdtempSync(path.join(webRoot, ".pixelflow-hook-tests-"));
const moduleDirectory = path.join(temporaryRoot, "modules");
const videoAgentModuleDirectory = path.join(temporaryRoot, "video-agent");
const apiDirectory = path.join(temporaryRoot, "api");
const tscEntry = path.join(webRoot, "node_modules", "typescript", "bin", "tsc");
const agentRuntimeContractFixture = path.resolve(
  webRoot,
  "..",
  "backend",
  "tests",
  "fixtures",
  "agent_runtime",
  "contracts-v1.json",
);
const supervisorLegacyAdapterFixture = path.join(
  webRoot,
  "tests",
  "fixtures",
  "supervisorLegacySnapshots.json",
);
const generatedAgentRuntimeTypeTest = path.join(
  typeTestRoot,
  "canonicalFixture.type-test.ts",
);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: webRoot,
    env: { ...process.env, ...options.env },
    stdio: "inherit",
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`测试命令执行失败，退出码：${result.status ?? "未知"}`);
  }
}

function compileStandaloneModules(sourceFiles) {
  run(process.execPath, [
    tscEntry,
    ...sourceFiles,
    "--target",
    "ES2022",
    "--module",
    "ES2022",
    "--moduleResolution",
    "bundler",
    "--rootDir",
    "src",
    "--outDir",
    moduleDirectory,
    "--skipLibCheck",
    "--strict",
  ]);
}

function compileVideoAgentModules() {
  run(process.execPath, [
    tscEntry,
    "src/features/video-agent/state/contracts.ts",
    "src/features/video-agent/state/reducer.ts",
    "src/features/video-agent/state/workspace.ts",
    "--target",
    "ES2022",
    "--module",
    "ES2022",
    "--moduleResolution",
    "bundler",
    "--rootDir",
    "src/features/video-agent/state",
    "--outDir",
    videoAgentModuleDirectory,
    "--skipLibCheck",
    "--strict",
  ]);
}

function checkContractTypes() {
  run(process.execPath, [
    tscEntry,
    "tests/agentRuntimeContracts.type-test.ts",
    "--target",
    "ES2022",
    "--module",
    "ES2022",
    "--moduleResolution",
    "bundler",
    "--noEmit",
    "--skipLibCheck",
    "--strict",
  ]);
}

function checkCanonicalFixtureTypes() {
  const fixture = JSON.parse(readFileSync(agentRuntimeContractFixture, "utf8"));
  const contractsWithoutExtension = path.join(webRoot, "src/lib/supervisor/contracts");
  const relativeContractsPath = path
    .relative(typeTestRoot, contractsWithoutExtension)
    .split(path.sep)
    .join("/");
  const contractsImport = relativeContractsPath.startsWith(".")
    ? relativeContractsPath
    : `./${relativeContractsPath}`;
  const generatedTypeTest = `
import type {
  ActionDecision,
  AgentInterruptProjection,
  AgentEventEnvelope,
  ContextEnvelope,
  ContextRequest,
  ContextSummary,
  ConversationOrchestration,
  ExternalJobRef,
  InterruptResponseRequest,
  OperationRequest,
  TurnRecord,
  TurnStartRequest,
  WorkflowRecord,
} from ${JSON.stringify(contractsImport)};

type CanonicalFixture = {
  schema_version: 1;
  orchestration: ConversationOrchestration;
  action_decision: ActionDecision;
  external_job_ref: ExternalJobRef;
  workflow_record: WorkflowRecord;
  turn_record: TurnRecord;
  context_summary: ContextSummary;
  context_envelope: ContextEnvelope;
  event: AgentEventEnvelope;
  turn_start_request: TurnStartRequest;
  interrupt_response_request: InterruptResponseRequest;
  interrupt_projection: AgentInterruptProjection;
  operation_request: OperationRequest;
  context_request: ContextRequest;
};

const fixture: CanonicalFixture = ${JSON.stringify(fixture)};
void fixture;
`;
  writeFileSync(generatedAgentRuntimeTypeTest, generatedTypeTest);
  run(process.execPath, [
    tscEntry,
    generatedAgentRuntimeTypeTest,
    "--target",
    "ES2022",
    "--module",
    "ES2022",
    "--moduleResolution",
    "bundler",
    "--noEmit",
    "--skipLibCheck",
    "--strict",
  ]);
}

function compileApiModule() {
  run(process.execPath, [
    tscEntry,
    "-p",
    "tests/tsconfig.api-auth.json",
    "--outDir",
    apiDirectory,
  ]);
  const apiPath = path.join(apiDirectory, "api.js");
  const compiledApi = readFileSync(apiPath, "utf8").replaceAll("@/lib/authStorage", "./authStorage.js");
  writeFileSync(apiPath, compiledApi);
}

function compileHookModule() {
  run(process.execPath, [
    tscEntry,
    "src/hooks/useSupervisorConversation.ts",
    "src/lib/authStorage.ts",
    "src/lib/supervisor/api.ts",
    "src/lib/supervisor/contracts.ts",
    "src/lib/supervisor/events.ts",
    "src/lib/supervisor/reducer.ts",
    "--target",
    "ES2022",
    "--module",
    "ES2022",
    "--moduleResolution",
    "bundler",
    "--rootDir",
    "src",
    "--outDir",
    hookTestRoot,
    "--skipLibCheck",
    "--strict",
  ]);
  writeFileSync(path.join(hookTestRoot, "package.json"), JSON.stringify({ type: "module" }));
}

function moduleUrl(directory, fileName) {
  return pathToFileURL(path.join(directory, fileName)).href;
}

function standaloneModuleUrl(fileName) {
  return moduleUrl(moduleDirectory, path.join("lib", fileName));
}

try {
  if (!existsSync(tscEntry)) {
    throw new Error("未找到本地 TypeScript 编译器，请先执行 corepack pnpm install");
  }

  writeFileSync(path.join(temporaryRoot, "package.json"), JSON.stringify({ type: "module" }));
  checkContractTypes();
  checkCanonicalFixtureTypes();

  if (contractOnly) {
    compileStandaloneModules(["src/lib/supervisor/contracts.ts"]);
  } else {
    compileStandaloneModules([
      "src/lib/activePlanSnapshot.ts",
      "src/lib/assetPackageProgressAnchor.ts",
      "src/lib/authStorage.ts",
      "src/lib/conversationRouting.ts",
      "src/lib/imageReview.ts",
      "src/lib/jianyingDraft.ts",
      "src/lib/planJobRecovery.ts",
      "src/lib/planMessageRecovery.ts",
      "src/lib/reviewWindow.ts",
      "src/lib/sceneAssetFailures.ts",
      "src/lib/sceneAssetModelSelection.ts",
      "src/lib/sceneMentions.ts",
      "src/lib/scenePackageAssetUi.ts",
      "src/lib/scenePackageJobResume.ts",
      "src/lib/scenePackages.ts",
      "src/lib/shotDescriptionDisplay.ts",
      "src/lib/supervisor/api.ts",
      "src/lib/supervisor/actions.ts",
      "src/lib/supervisor/contracts.ts",
      "src/lib/supervisor/events.ts",
      "src/lib/supervisor/legacyAdapter.ts",
      "src/lib/supervisor/reducer.ts",
      "src/lib/supervisor/runtimeNotice.ts",
      "src/lib/supervisor/turnSubmission.ts",
      "src/lib/supervisor/workspaceProjection.ts",
      "src/lib/time.ts",
      "src/lib/videoRequirementConfig.ts",
      "src/lib/workflowTaskBoard.ts",
    ]);
    compileApiModule();
    compileHookModule();
    compileVideoAgentModules();
  }

  const testFiles = contractOnly
    ? [path.join(webRoot, "tests", "agentRuntimeContracts.test.mjs")]
    : readdirSync(path.join(webRoot, "tests"))
      .filter((fileName) => fileName.endsWith(".test.mjs"))
      .sort()
      .map((fileName) => path.join(webRoot, "tests", fileName));

  run(process.execPath, ["--test", ...testFiles], {
    env: {
      ACTIVE_PLAN_SNAPSHOT_TEST_MODULE: standaloneModuleUrl("activePlanSnapshot.js"),
      ASSET_PACKAGE_PROGRESS_ANCHOR_TEST_MODULE: standaloneModuleUrl("assetPackageProgressAnchor.js"),
      AGENT_RUNTIME_CONTRACTS_TEST_MODULE: standaloneModuleUrl("supervisor/contracts.js"),
      AGENT_RUNTIME_CONTRACT_FIXTURE: agentRuntimeContractFixture,
      AGENT_RUNTIME_GENERATED_TYPE_TEST: generatedAgentRuntimeTypeTest,
      API_TEST_MODULE: moduleUrl(apiDirectory, "api.js"),
      AUTH_STORAGE_TEST_MODULE: standaloneModuleUrl("authStorage.js"),
      CONVERSATION_ROUTING_TEST_MODULE: standaloneModuleUrl("conversationRouting.js"),
      IMAGE_REVIEW_TEST_MODULE: standaloneModuleUrl("imageReview.js"),
      JIANYING_DRAFT_TEST_MODULE: standaloneModuleUrl("jianyingDraft.js"),
      PLAN_JOB_RECOVERY_TEST_MODULE: standaloneModuleUrl("planJobRecovery.js"),
      PLAN_MESSAGE_RECOVERY_TEST_MODULE: standaloneModuleUrl("planMessageRecovery.js"),
      REVIEW_WINDOW_TEST_MODULE: standaloneModuleUrl("reviewWindow.js"),
      SCENE_ASSET_FAILURES_TEST_MODULE: path.join(moduleDirectory, "lib/sceneAssetFailures.js"),
      SCENE_ASSET_MODEL_SELECTION_TEST_MODULE: standaloneModuleUrl("sceneAssetModelSelection.js"),
      SCENE_MENTIONS_TEST_MODULE: standaloneModuleUrl("sceneMentions.js"),
      SCENE_PACKAGE_ASSET_UI_TEST_MODULE: standaloneModuleUrl("scenePackageAssetUi.js"),
      SCENE_PACKAGE_JOB_RESUME_TEST_MODULE: standaloneModuleUrl("scenePackageJobResume.js"),
      SCENE_PACKAGES_TEST_MODULE: standaloneModuleUrl("scenePackages.js"),
      SHOT_DESCRIPTION_DISPLAY_TEST_MODULE: standaloneModuleUrl("shotDescriptionDisplay.js"),
      SUPERVISOR_API_TEST_MODULE: standaloneModuleUrl("supervisor/api.js"),
      SUPERVISOR_ACTIONS_TEST_MODULE: standaloneModuleUrl("supervisor/actions.js"),
      SUPERVISOR_EVENTS_TEST_MODULE: standaloneModuleUrl("supervisor/events.js"),
      SUPERVISOR_HOOK_TEST_MODULE: moduleUrl(hookTestRoot, "hooks/useSupervisorConversation.js"),
      SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE: moduleUrl(
        moduleDirectory,
        "lib/supervisor/legacyAdapter.js",
      ),
      SUPERVISOR_LEGACY_ADAPTER_FIXTURE: supervisorLegacyAdapterFixture,
      SUPERVISOR_REDUCER_TEST_MODULE: standaloneModuleUrl("supervisor/reducer.js"),
      SUPERVISOR_RUNTIME_NOTICE_TEST_MODULE: moduleUrl(
        moduleDirectory,
        "lib/supervisor/runtimeNotice.js",
      ),
      SUPERVISOR_TURN_SUBMISSION_TEST_MODULE: moduleUrl(
        moduleDirectory,
        "lib/supervisor/turnSubmission.js",
      ),
      SUPERVISOR_WORKSPACE_PROJECTION_TEST_MODULE: moduleUrl(
        moduleDirectory,
        "lib/supervisor/workspaceProjection.js",
      ),
      TIME_TEST_MODULE: standaloneModuleUrl("time.js"),
      VIDEO_AGENT_TIMELINE_REDUCER_TEST_MODULE: moduleUrl(videoAgentModuleDirectory, "reducer.js"),
      VIDEO_AGENT_WORKSPACE_PROJECTION_TEST_MODULE: moduleUrl(videoAgentModuleDirectory, "workspace.js"),
      VIDEO_REQUIREMENT_CONFIG_TEST_MODULE: standaloneModuleUrl("videoRequirementConfig.js"),
      WORKFLOW_TASK_BOARD_TEST_MODULE: standaloneModuleUrl("workflowTaskBoard.js"),
    },
  });
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
  rmSync(typeTestRoot, { recursive: true, force: true });
  rmSync(hookTestRoot, { recursive: true, force: true });
}
