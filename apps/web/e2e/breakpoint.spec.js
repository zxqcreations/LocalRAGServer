import { expect, test } from "@playwright/test";

test("移动断点下管理端可用（320px）", async ({ page }) => {
  await page.goto("/#/");
  await expect(page.locator("h2", { hasText: "知识库管理" })).toBeVisible();
  // 窄屏下导航仍可点击
  await page.click("text=系统监控");
  await expect(page.locator("h2", { hasText: "系统监控" })).toBeVisible();
});
