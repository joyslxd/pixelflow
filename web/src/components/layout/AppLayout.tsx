import { Outlet } from "react-router-dom";
import { Bell } from "lucide-react";
import { Sidebar } from "./Sidebar";

export function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-end gap-4 px-6">
          <button className="text-ink-soft hover:text-ink" aria-label="通知">
            <Bell size={18} />
          </button>
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-[13px] font-semibold text-white">
            A
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
