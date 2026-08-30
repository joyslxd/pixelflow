import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createHashRouter, Navigate, RouterProvider } from "react-router-dom";

import "./index.css";
import { AppLayout } from "@/components/layout/AppLayout";
import { AuthTokenPage } from "@/pages/AuthTokenPage";
import { TracePage } from "@/pages/TracePage";
import { AgentWorkspace } from "@/features/agent-workspace/AgentWorkspace";
import { setupContentAppAuthorizationListener } from "@/lib/authStorage";

setupContentAppAuthorizationListener();

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

const router = createHashRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <AgentWorkspace /> },
      { path: "c/:conversationId", element: <AgentWorkspace /> },
      { path: "auth-token", element: <AuthTokenPage /> },
      { path: "trace/:conversationId", element: <TracePage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
