import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/#/");
  await expect(page.locator("h2", { hasText: "知识库管理" })).toBeVisible();
});

test("五模块导航可达", async ({ page }) => {
  await page.click("text=检索调试台");
  await expect(page.locator("h2", { hasText: "检索调试台" })).toBeVisible();
  await page.click("text=API Key");
  await expect(page.locator("h2", { hasText: "API Key 管理" })).toBeVisible();
  await page.click("text=系统监控");
  await expect(page.locator("h2", { hasText: "系统监控" })).toBeVisible();
  await page.click("text=评估面板");
  await expect(page.locator("h2", { hasText: "评估面板" })).toBeVisible();
});

test("API Key 签发与吊销闭环", async ({ page }) => {
  await page.click("text=API Key");
  // 回归（代码审查 LOW-1）：页面刷新后 CSRF token 必须仍可用——
  // 修复前 token 仅存模块内存，刷新后签发必 403
  await page.reload();
  await expect(page.locator("h2", { hasText: "API Key 管理" })).toBeVisible();
  await page.fill('input[placeholder="agent-key"]', "e2e-key");
  await page.click("text=签发");
  await expect(page.locator("text=明文 Key")).toBeVisible();
  await page.click("text=吊销");
  await expect(page.locator("text=e2e-key")).toHaveCount(0);
});
