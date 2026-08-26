import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

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
const contractSource = path.join(webRoot, "src", "api", "contracts.ts");
const testFile = path.join(webRoot, "tests", "harnessRuntimeContracts.test.mjs");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: webRoot,
    env: { ...process.env, ...options.env },
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`测试命令执行失败，退出码：${result.status ?? "未知"}`);
}

if (!existsSync(tscEntry)) {
  throw new Error("未找到本地 TypeScript 编译器，请先执行 corepack pnpm install");
}
if (!existsSync(fixture) || !existsSync(contractSource)) {
  throw new Error("Harness 跨端合同或 fixture 缺失");
}

// 先编译全部当前前端源码，再运行唯一的 Harness 跨端合同门禁。
run(process.execPath, [tscEntry, "--noEmit"]);
run(process.execPath, ["--test", testFile], {
  env: {
    AGENT_HARNESS_CONTRACT_FIXTURE: fixture,
    AGENT_HARNESS_TYPES_SOURCE: contractSource,
  },
});
