import { Outlet } from "react-router-dom";

export function AppLayout() {
  return (
    <main className="h-screen overflow-hidden bg-canvas">
      <Outlet />
    </main>
  );
}
