import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url)); // apps/web/e2e

/** 读取 API 启动时生成的初始管理密码（web-admin-auth.md §1 的一次性密码流程）。
 *
 * 绝对路径派生自本文件位置（代码审查 LOW-2）：从任意 cwd 运行 Playwright 均可用。
 */
export function readInitialPassword() {
  const file = join(HERE, "..", "..", ".e2e-data", "admin_initial_password");
  return readFileSync(file, "utf-8").trim();
}

export async function login(page, password) {
  await page.goto("/#/login");
  await page.fill('input[autocomplete="username"]', "admin");
  await page.fill('input[autocomplete="current-password"]', password);
  await page.click('button[type="submit"]');
}
