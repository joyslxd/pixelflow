import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// 后端网关地址(Makefile dev 默认 8001);dev 下把 /agent 代理过去,避免 CORS。
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8001";

export default defineConfig({
  base: "/agentfrontend/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5273,
    proxy: {
      "^/agent(/|$)": { target: API_TARGET, changeOrigin: false },
    },
  },
});
