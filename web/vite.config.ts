import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// 后端网关地址(Makefile dev 默认 8001);dev 下把 /agent 代理过去,避免 CORS。
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8001";
// 本地单独调试前端时，附件上传仍应走 content-app 的 /api/upload。
const CONTENT_APP_TARGET = process.env.VITE_CONTENT_APP_TARGET ?? "https://test-video.borgrise.com";

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
      "^/api/upload$": { target: CONTENT_APP_TARGET, changeOrigin: true, secure: false },
    },
  },
});
