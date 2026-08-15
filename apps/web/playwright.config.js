import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:5173",
  },
  projects: [
    // 认证引导：一次性初始密码登录 + 强制改密，产出 storageState 供其余项目复用
    { name: "setup", testMatch: /auth\.setup\.js/ },
    {
      name: "e2e",
      testMatch: /.*\.spec\.js/,
      testIgnore: /.*breakpoint\.spec\.js/,
      use: {
        viewport: { width: 1440, height: 900 },
        storageState: "e2e/.auth/state.json",
      },
      dependencies: ["setup"],
    },
    {
      name: "breakpoints",
      testMatch: /.*breakpoint\.spec\.js/,
      use: {
        viewport: { width: 320, height: 640 },
        storageState: "e2e/.auth/state.json",
      },
      dependencies: ["setup"],
    },
  ],
  webServer: [
    // 包装脚本在拉起 uvicorn 前清空 .e2e-data（Qdrant 本地模式会锁目录，
    // 清理必须早于进程启动，因此不能用 globalSetup）
    {
      command: "node scripts/run-e2e-api.mjs",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
    },
    {
      command: "npm run dev",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
    },
  ],
});
