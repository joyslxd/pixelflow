import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// 本地开发默认把 /api 代理到当前后端端口 8002, 也允许通过环境变量覆盖。
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8002";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5273,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: false },
    },
  },
});
