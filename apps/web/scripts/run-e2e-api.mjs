import { spawn, spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** E2E API 启动包装：先清空 .e2e-data，再拉起 uvicorn。
 *
 * Playwright 的 webServer 先于 globalSetup 启动，若在 globalSetup 里删数据目录，
 * 已被 uvicorn（Qdrant 本地模式文件锁）锁住 → Windows 下 EPERM。
 * 清理必须在 uvicorn 打开任何文件之前完成，因此收进启动命令本身。
 */
const appsDir = join(dirname(fileURLToPath(import.meta.url)), "..", ".."); // apps/
const dataDir = join(appsDir, ".e2e-data");

try {
  rmSync(dataDir, { recursive: true, force: true });
} catch (err) {
  console.error(
    "[run-e2e-api] 无法清空 .e2e-data（可能有残留 e2e API 进程锁住数据目录）：",
    err.message
  );
  console.error("[run-e2e-api] 排查：tasklist | findstr python，然后 taskkill /F /T /PID <残留进程>");
  process.exit(1);
}

const child = spawn(
  "uv",
  [
    "run",
    "uvicorn",
    "apps.api.main:create_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
  ],
  {
    cwd: appsDir,
    stdio: "inherit",
    env: {
      ...process.env,
      RAG_EMBEDDING_BACKEND: "stub",
      RAG_EMBEDDING_DIM: "64",
      // 仅测试桩主 Key；环境变量可覆盖（与生产主 Key 无交集）
      RAG_API_KEY: process.env.RAG_API_KEY ?? "e2e-master-key",
      RAG_DATA_DIR: "./.e2e-data",
    },
  }
);

let stopping = false;

function cleanup() {
  // Windows 上只杀直接子进程会遗留孙进程（uv → python/uvicorn）占用端口与
  // Qdrant 文件锁（代码审查 MEDIUM-2 / 安全审查 M-1）：win32 走 taskkill 树杀
  stopping = true;
  if (process.platform === "win32" && child.pid) {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
  } else {
    child.kill("SIGTERM");
  }
}

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, cleanup);
}

child.on("error", (err) => {
  console.error("[run-e2e-api] 无法启动 uvicorn:", err.message);
  process.exit(1);
});

child.on("exit", (code) => process.exit(stopping ? 0 : (code ?? 0)));
