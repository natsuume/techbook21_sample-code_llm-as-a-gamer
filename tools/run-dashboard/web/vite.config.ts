/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "dist",
  },
  server: {
    // API の要求を run-dashboard のサーバーへ中継する。Host を localhost:5173 のまま届けるため changeOrigin を
    // 付けない (文字列だけの書き方は changeOrigin: true になるため、オブジェクトで target だけを書く)
    proxy: {
      "/api": { target: "http://127.0.0.1:8765" },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
