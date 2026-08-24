import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/admin": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        proxyTimeout: 300_000,
        timeout: 300_000,
      },
      // 公开 API 也走代理（供开发时文件上传等使用）
      "/api/v1/kb": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
