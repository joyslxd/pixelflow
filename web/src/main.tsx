import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";

import "./index.css";
import { AppLayout } from "@/components/layout/AppLayout";
import { AuthTokenPage } from "@/pages/AuthTokenPage";
import { WorkspacePage } from "@/pages/WorkspacePage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <WorkspacePage /> },
      { path: "auth-token", element: <AuthTokenPage /> },
      // 当前路由骨架已预留 /c/:taskId，但 WorkspacePage 还没有用 taskId 恢复历史任务。
      { path: "c/:taskId", element: <WorkspacePage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
], {
  basename: "/agentfrontend",
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
