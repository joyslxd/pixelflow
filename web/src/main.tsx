import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";

import "./index.css";
import { AppLayout } from "@/components/layout/AppLayout";
import { AuthTokenPage } from "@/pages/AuthTokenPage";
import { WorkspacePage } from "@/pages/WorkspacePage";
import { setupContentAppAuthorizationListener } from "@/lib/authStorage";

setupContentAppAuthorizationListener();

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
      { path: "c/:conversationId", element: <WorkspacePage /> },
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
