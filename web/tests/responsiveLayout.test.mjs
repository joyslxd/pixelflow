import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const sidebarSource = readFileSync(
  fileURLToPath(new URL("../src/components/layout/Sidebar.tsx", import.meta.url)),
  "utf8",
);
const appLayoutSource = readFileSync(
  fileURLToPath(new URL("../src/components/layout/AppLayout.tsx", import.meta.url)),
  "utf8",
);
const canvasPanelSource = readFileSync(
  fileURLToPath(new URL("../src/components/canvas/CanvasPanel.tsx", import.meta.url)),
  "utf8",
);
const planEditorSource = readFileSync(
  fileURLToPath(new URL("../src/components/canvas/PlanMarkdownEditor.tsx", import.meta.url)),
  "utf8",
);
const storyboardPanelSource = readFileSync(
  fileURLToPath(new URL("../src/components/canvas/StoryboardPanel.tsx", import.meta.url)),
  "utf8",
);

test("移动端隐藏固定宽度历史侧栏并让主工作区占满宽度", () => {
  assert.match(
    sidebarSource,
    /className="[^"]*\bhidden\b[^"]*\bxl:flex\b[^"]*w-\[244px\]/,
    "历史侧栏必须默认隐藏，只在 1280px 以上桌面断点显示",
  );
  assert.match(
    appLayoutSource,
    /className="[^"]*\bmin-w-0\b[^"]*\bflex-1\b[^"]*"/,
    "主工作区必须允许在窄屏内收缩并占满剩余宽度",
  );
});

test("移动端画布以全屏层展示并取消桌面最小宽度", () => {
  for (const [name, source] of [
    ["通用画布", canvasPanelSource],
    ["Plan 编辑器", planEditorSource],
    ["分镜编辑器", storyboardPanelSource],
  ]) {
    assert.match(source, /\bfixed\b[^"]*\binset-0\b[^"]*\bz-50\b/, `${name} 必须在移动端全屏覆盖`);
    assert.match(source, /\bw-full\b[^"]*\bmin-w-0\b/, `${name} 移动端不得保留桌面最小宽度`);
    assert.match(source, /\bxl:static\b/, `${name} 必须在 1280px 以上桌面断点恢复分栏布局`);
  }
  assert.match(
    storyboardPanelSource,
    /grid-cols-1[^"]*xl:grid-cols-\[minmax\(0,1fr\)_minmax\(280px,42%\)\]/,
    "分镜编辑器在 1280px 以下必须从双列折叠为单列",
  );
});
