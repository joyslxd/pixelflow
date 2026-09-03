import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tscEntry = path.join(webRoot, "node_modules", "typescript", "bin", "tsc");
const fixture = path.resolve(
  webRoot,
  "..",
  "backend",
  "tests",
  "fixtures",
  "agent_harness",
  "contracts-v1.json",
);
const harnessSnapshotFixture = path.resolve(
  webRoot,
  "..",
  "backend",
  "tests",
  "fixtures",
  "agent_runtime",
  "harness-snapshot-v1.json",
);
const contractSource = path.join(webRoot, "src", "api", "contracts.ts");
const harnessContractTest = path.join(webRoot, "tests", "harnessRuntimeContracts.test.mjs");
const reducerTest = path.join(webRoot, "tests", "agentRuntimeReducer.test.mjs");
const f4SourceGateTest = path.join(webRoot, "tests", "f4SourceGate.test.mjs");
const workspaceV2Test = path.join(webRoot, "tests", "workspaceV2.test.mjs");
const conversationScrollTest = path.join(webRoot, "tests", "conversationScroll.test.mjs");
const contentAppOriginTest = path.join(webRoot, "tests", "contentAppOrigin.test.mjs");
const temporaryRoot = mkdtempSync(path.join(os.tmpdir(), "pixelflow-web-tests-"));
const moduleDirectory = path.join(temporaryRoot, "modules");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: webRoot,
    env: { ...process.env, ...options.env },
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`测试命令执行失败，退出码：${result.status ?? "未知"}`);
}

function compileAgentRuntimeModules() {
  run(process.execPath, [
    tscEntry,
    "src/api/contracts.ts",
    "src/features/agent-runtime/reducer.ts",
    "src/features/agent-runtime/snapshotProjector.ts",
    "src/features/agent-runtime/state.ts",
    "src/features/agent-runtime/workspaceV2.ts",
    "src/lib/conversationScroll.ts",
    "src/lib/contentAppOrigin.ts",
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

try {
  if (!existsSync(tscEntry)) {
    throw new Error("未找到本地 TypeScript 编译器，请先执行 corepack pnpm install");
  }
  if (!existsSync(fixture) || !existsSync(contractSource)) {
    throw new Error("Harness 跨端合同或 fixture 缺失");
  }
  if (!existsSync(harnessSnapshotFixture)) {
    throw new Error("F0 Snapshot fixture 缺失");
  }

  writeFileSync(path.join(temporaryRoot, "package.json"), JSON.stringify({ type: "module" }));
  compileAgentRuntimeModules();

  // 先编译全部当前前端源码，再运行公开合同与 reducer 门禁。
  run(process.execPath, [tscEntry, "--noEmit"]);
  run(process.execPath, ["--test", harnessContractTest, reducerTest, f4SourceGateTest, workspaceV2Test, conversationScrollTest, contentAppOriginTest], {
    env: {
      AGENT_HARNESS_CONTRACT_FIXTURE: fixture,
      AGENT_HARNESS_TYPES_SOURCE: contractSource,
      AGENT_RUNTIME_REDUCER_TEST_MODULE: pathToFileURL(
        path.join(moduleDirectory, "features/agent-runtime/state.js"),
      ).href,
      AGENT_RUNTIME_SNAPSHOT_FIXTURE: harnessSnapshotFixture,
      WORKSPACE_V2_TEST_MODULE: pathToFileURL(
        path.join(moduleDirectory, "features/agent-runtime/workspaceV2.js"),
      ).href,
      CONVERSATION_SCROLL_TEST_MODULE: pathToFileURL(
        path.join(moduleDirectory, "lib/conversationScroll.js"),
      ).href,
      CONTENT_APP_ORIGIN_TEST_MODULE: pathToFileURL(
        path.join(moduleDirectory, "lib/contentAppOrigin.js"),
      ).href,
      PIXELFLOW_WEB_ROOT: webRoot,
    },
  });
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}
