/** 对话优先工作台：Workspace 默认收起，按需从右侧展开。 */

import { useState, type ReactNode } from "react";

type WorkspaceShellProps = {
  sidebar: ReactNode;
  header: ReactNode;
  messages: ReactNode;
  composer: ReactNode;
  workspace: ReactNode;
};

export function WorkspaceShell({
  sidebar,
  header,
  messages,
  composer,
  workspace,
}: WorkspaceShellProps) {
  const [workspaceOpen, setWorkspaceOpen] = useState(false);

  return (
    <main className="relative grid h-full min-h-0 grid-cols-[220px_minmax(0,1fr)] overflow-hidden bg-line">
      {sidebar}
      <section className="flex min-h-0 min-w-0 flex-col bg-surface">
        <header className="flex items-center justify-between border-b border-line px-5 py-3 text-sm">
          {header}
        </header>
        {messages}
        <div className="shrink-0 border-t border-line p-4">{composer}</div>
      </section>
      {!workspaceOpen ? (
        <button
          type="button"
          className="absolute right-0 top-1/2 z-20 grid h-16 w-9 translate-x-0 -translate-y-1/2 place-items-center rounded-l-full border border-r-0 border-line bg-surface text-lg text-ink-soft shadow-md hover:text-accent"
          aria-label="展开工作空间"
          aria-expanded="false"
          onClick={() => setWorkspaceOpen(true)}
        >
          ‹
        </button>
      ) : null}
      {workspaceOpen ? (
        <aside className="absolute inset-y-0 right-0 z-30 w-[min(680px,calc(100vw-240px))] overflow-y-auto border-l border-line bg-canvas text-sm shadow-2xl" aria-label="工作空间">
          <header className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-surface px-5 py-4">
            <div>
              <p className="text-sm font-semibold text-ink">工作空间</p>
              <p className="mt-1 text-xs text-ink-soft">创意、脚本、资产与执行状态</p>
            </div>
            <button
              type="button"
              className="grid h-8 w-8 place-items-center rounded-lg text-lg text-ink-soft hover:bg-canvas hover:text-ink"
              aria-label="关闭工作空间"
              onClick={() => setWorkspaceOpen(false)}
            >
              ×
            </button>
          </header>
          <div className="p-5">{workspace}</div>
        </aside>
      ) : null}
    </main>
  );
}
