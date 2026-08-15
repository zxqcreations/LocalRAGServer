import { expect, test } from "@playwright/test";
import { login, readInitialPassword } from "./helpers.js";

/** 认证引导（auth-setup 项目）：登录 → 强制改密 → 保存 storageState。
 *
 * 其余项目通过 dependencies + storageState 复用会话（Cookie + localStorage 中的
 * CSRF token），不再各自登录，避免多 worker 并发竞争一次性初始密码。
 */
test("登录并强制修改初始密码", async ({ page }) => {
  const initial = readInitialPassword();
  await login(page, initial);
  // 强制改密表单出现（web-admin-auth.md §1）
  await expect(page.locator("text=首次登录须修改密码")).toBeVisible();
  await page.locator('input[type="password"]').nth(1).fill("e2e-admin-password-1");
  await page.click('button:has-text("修改密码")');
  await expect(page.locator("h2", { hasText: "知识库管理" })).toBeVisible();
  await page.context().storageState({ path: "e2e/.auth/state.json" });
});
