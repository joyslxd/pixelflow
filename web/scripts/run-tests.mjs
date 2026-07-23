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
const moduleDirectory = path.join(temporaryRoot, "modules");
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
    "--outDir",
    moduleDirectory,
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

function moduleUrl(directory, fileName) {
  return pathToFileURL(path.join(directory, fileName)).href;
}

try {
  if (!existsSync(tscEntry)) {
    throw new Error("未找到本地 TypeScript 编译器，请先执行 corepack pnpm install");
  }

  writeFileSync(path.join(temporaryRoot, "package.json"), JSON.stringify({ type: "module" }));
  checkContractTypes();

  if (contractOnly) {
    compileStandaloneModules(["src/lib/supervisor/contracts.ts"]);
  } else {
    compileStandaloneModules([
      "src/lib/activePlanSnapshot.ts",
      "src/lib/authStorage.ts",
      "src/lib/conversationRouting.ts",
      "src/lib/imageReview.ts",
      "src/lib/jianyingDraft.ts",
      "src/lib/planMessageRecovery.ts",
      "src/lib/reviewWindow.ts",
      "src/lib/sceneAssetFailures.ts",
      "src/lib/sceneMentions.ts",
      "src/lib/scenePackages.ts",
      "src/lib/supervisor/contracts.ts",
      "src/lib/time.ts",
      "src/lib/videoRequirementConfig.ts",
      "src/lib/workflowTaskBoard.ts",
    ]);
    compileApiModule();
  }

  const testFiles = contractOnly
    ? [path.join(webRoot, "tests", "agentRuntimeContracts.test.mjs")]
    : readdirSync(path.join(webRoot, "tests"))
      .filter((fileName) => fileName.endsWith(".test.mjs"))
      .sort()
      .map((fileName) => path.join(webRoot, "tests", fileName));

  run(process.execPath, ["--test", ...testFiles], {
    env: {
      ACTIVE_PLAN_SNAPSHOT_TEST_MODULE: moduleUrl(moduleDirectory, "activePlanSnapshot.js"),
      AGENT_RUNTIME_CONTRACTS_TEST_MODULE: moduleUrl(
        moduleDirectory,
        contractOnly ? "contracts.js" : "supervisor/contracts.js",
      ),
      AGENT_RUNTIME_CONTRACT_FIXTURE: agentRuntimeContractFixture,
      API_TEST_MODULE: moduleUrl(apiDirectory, "api.js"),
      AUTH_STORAGE_TEST_MODULE: moduleUrl(moduleDirectory, "authStorage.js"),
      CONVERSATION_ROUTING_TEST_MODULE: moduleUrl(moduleDirectory, "conversationRouting.js"),
      IMAGE_REVIEW_TEST_MODULE: moduleUrl(moduleDirectory, "imageReview.js"),
      JIANYING_DRAFT_TEST_MODULE: moduleUrl(moduleDirectory, "jianyingDraft.js"),
      PLAN_MESSAGE_RECOVERY_TEST_MODULE: moduleUrl(moduleDirectory, "planMessageRecovery.js"),
      REVIEW_WINDOW_TEST_MODULE: moduleUrl(moduleDirectory, "reviewWindow.js"),
      SCENE_ASSET_FAILURES_TEST_MODULE: path.join(moduleDirectory, "sceneAssetFailures.js"),
      SCENE_MENTIONS_TEST_MODULE: moduleUrl(moduleDirectory, "sceneMentions.js"),
      SCENE_PACKAGES_TEST_MODULE: moduleUrl(moduleDirectory, "scenePackages.js"),
      TIME_TEST_MODULE: moduleUrl(moduleDirectory, "time.js"),
      VIDEO_REQUIREMENT_CONFIG_TEST_MODULE: moduleUrl(moduleDirectory, "videoRequirementConfig.js"),
      WORKFLOW_TASK_BOARD_TEST_MODULE: moduleUrl(moduleDirectory, "workflowTaskBoard.js"),
    },
  });
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}
