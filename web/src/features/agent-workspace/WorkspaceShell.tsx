/** 三栏工作台外壳：对话列表、消息区、只读 Workspace 摘要。 */

import type { ReactNode } from "react";

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
  return (
    <main className="grid h-full min-h-0 grid-cols-[220px_minmax(0,1fr)_300px] gap-px bg-line">
      {sidebar}
      <section className="flex min-w-0 flex-col bg-surface">
        <header className="flex items-center justify-between border-b border-line px-5 py-3 text-sm">
          {header}
        </header>
        {messages}
        <div className="border-t border-line p-4">{composer}</div>
      </section>
      <aside className="min-h-0 overflow-y-auto bg-surface p-4 text-sm">{workspace}</aside>
    </main>
  );
}
