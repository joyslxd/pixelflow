import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";

import "./index.css";
import { AppLayout } from "@/components/layout/AppLayout";
import { AuthGate } from "@/components/auth/AuthGate";
import { WorkspacePage } from "@/pages/WorkspacePage";
import { TracePage } from "@/pages/TracePage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: (
      <AuthGate>
        <AppLayout />
      </AuthGate>
    ),
    children: [
      { index: true, element: <WorkspacePage /> },
      { path: "trace", element: <TracePage /> },
      { path: "c/:taskId", element: <WorkspacePage /> },
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
