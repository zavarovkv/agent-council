import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const origin = "https://council.example.test";
const path = "/auth/login/telegram-widget";
const nonce = "test-browser-nonce-not-a-production-credential";
const token = [
  Buffer.from(JSON.stringify({ alg: "RS256" })).toString("base64url"),
  Buffer.from(JSON.stringify({ sub: "test-owner", id: 12345, nonce })).toString("base64url"),
  "test-signature-never-sent-to-real-backend",
].join(".");

async function mount(page, { deny = false, sdkUnavailable = false } = {}) {
  const html = await readFile(".browser-fixture/login.html", "utf8");
  const submissions = [];
  await page.route(origin + "/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === path + "/challenge") {
      return route.fulfill({ json: { client_id: "123456789", nonce } });
    }
    if (url.pathname === path + "/complete") {
      submissions.push(request.postDataJSON());
      return route.fulfill({ status: deny ? 403 : 200, json: deny ? { detail: "Доступ не разрешен." } : { next: "/sessions" } });
    }
    if (url.pathname === "/auth/login/council/team") {
      const roles = [["qui-gon", "GPT-6.1 Sol"], ["dexter", "GPT-6 Astra"], ["lando", "GPT-6.1 Sol"], ["padme", "GPT-6 Astra"]];
      return route.fulfill({ json: { schema: 1, agents: roles.map(([slug, name]) => ({
        slug, model: { name, effort: "high" }, closed_tasks: slug === "padme" ? null : 3,
        tokens: 123456, linear_connected: slug !== "padme",
      })) } });
    }
    if (url.pathname.startsWith("/auth/login/council/avatars/")) {
      const slug = url.pathname.split("/").pop();
      const filename = { "qui-gon": "qui-gon.png", dexter: "dexter.png", lando: "lando.png", padme: "padme.png" }[slug];
      return route.fulfill({ contentType: "image/png", body: await readFile(`assets/agents/${filename}`) });
    }
    return route.fulfill({ contentType: "text/html", body: url.pathname === "/sessions" ? "<h1>Authorized test fixture</h1>" : html });
  });
  // The real SDK runs, but the provider popup is a fixture: no real account or bot is contacted.
  await page.context().route("https://oauth.telegram.org/auth?**", (route) => route.fulfill({
    contentType: "text/html", body: "<title>Telegram test popup</title><p>Test login provider</p>",
  }));
  if (sdkUnavailable) await page.route("https://oauth.telegram.org/js/telegram-login.js?6", (route) => route.abort());
  await page.goto(origin + "/login", { waitUntil: "networkidle" });
  return submissions;
}

for (const colorScheme of ["light", "dark"]) {
  test(`official widget opens its popup and posts the result (${colorScheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    await page.setViewportSize(colorScheme === "dark" ? { width: 390, height: 844 } : { width: 1440, height: 900 });
    const submissions = await mount(page);
    const button = page.getByRole("button", { name: "Войти через Telegram" });
    await expect(button).toBeVisible();
    await expect(button).toBeEnabled();
    await expect(page.locator("header nav button.tg-auth-button")).toHaveCount(1);
    await expect(page.locator("footer")).toHaveCount(1);
    await expect(page.locator("footer")).toContainText("© 2026");
    await expect(page.locator("footer a")).toHaveCount(0);
    await expect(button).toHaveAttribute("data-style", colorScheme === "dark" ? "icon" : "");
    if (colorScheme === "dark") {
      expect(await button.evaluate(element => getComputedStyle(element).fontSize)).toBe("0px");
      expect((await button.boundingBox()).width).toBe(44);
    }
    await expect(page.locator("[data-agent]")).toHaveCount(4);
    await expect(page.locator('[data-agent="dexter"] [data-field="model"]')).toHaveText("GPT-6 Astra · high");
    await expect(page.locator('[data-agent="padme"] [data-field="closed_tasks"]')).toHaveText("—");
    await expect(page.locator('[data-agent="padme"] [data-field="connection"]')).toHaveText("Подключение к Linear не завершено");
    for (const image of await page.locator("[data-avatar]").all()) {
      await image.scrollIntoViewIfNeeded();
      await expect.poll(() => image.evaluate(element => element.naturalWidth)).toBeGreaterThan(0);
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    if (process.env.COUNCIL_SCREENSHOTS) await page.screenshot({ path: `${process.env.COUNCIL_SCREENSHOTS}/${colorScheme}.png`, fullPage: true });
    await page.keyboard.press("Tab");
    await expect(button).toBeFocused();
    // The unmodified SDK button uses the browser's native keyboard-focus ring.
    const focus = await button.evaluate((element) => ({
      visible: element.matches(":focus-visible"),
      style: getComputedStyle(element).outlineStyle,
      width: parseFloat(getComputedStyle(element).outlineWidth),
    }));
    expect(focus.visible).toBeTruthy();
    expect(focus.style).not.toBe("none");
    expect(focus.width).toBeGreaterThan(0);
    const popupPromise = page.waitForEvent("popup");
    if (colorScheme === "light") await button.press("Enter");
    else await button.click();
    const popup = await popupPromise;
    await popup.waitForLoadState();
    const target = new URL(popup.url());
    expect(target.origin).toBe("https://oauth.telegram.org");
    expect(target.searchParams.get("response_type")).toBe("post_message");
    expect(target.searchParams.get("nonce")).toBe(nonce);
    expect(target.searchParams.get("scope")).toBe("openid profile");
    await popup.evaluate(({ token, origin }) => opener.postMessage({ event: "auth_result", result: token }, origin), { token, origin });
    await expect(page).toHaveURL(origin + "/sessions");
    expect(submissions).toEqual([{ id_token: token }]);
    expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  });
}

test("denied account stays on the landing and can retry", async ({ page }) => {
  const submissions = await mount(page, { deny: true });
  const button = page.getByRole("button", { name: "Войти через Telegram" });
  const popupPromise = page.waitForEvent("popup");
  await button.click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  await popup.evaluate(({ token, origin }) => opener.postMessage({ event: "auth_result", result: token }, origin), { token, origin });
  await expect(page.locator(".login-status")).toHaveText("Доступ не разрешен.");
  await expect(button).toBeEnabled();
  expect(page.url()).toBe(origin + "/login");
  expect(submissions).toHaveLength(1);
});

test("unavailable SDK keeps a functional server-side login link", async ({ page }) => {
  await mount(page, { sdkUnavailable: true });
  await expect(page.getByRole("link", { name: "Войти через Telegram" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Войти через Telegram" })).toBeHidden();
  await expect(page.locator(".login-status")).toContainText("Не удалось загрузить кнопку Telegram");
});

test("footer preferences persist, translate counters and keep the native mobile icon", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "Switch to English" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("h1")).toHaveText("Agent Council.");
  await expect(page.locator('[data-agent="dexter"]')).toContainText("Product researcher");
  await expect(page.locator('[data-agent="dexter"] [data-field="tokens"]')).toHaveText("123,456");
  await page.emulateMedia({ colorScheme: "light" });
  await page.getByRole("button", { name: "Switch theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.setViewportSize({ width: 375, height: 812 });
  const button = page.getByRole("button", { name: "Log in with Telegram" });
  await expect(button).toHaveAttribute("data-style", "icon");
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(button).toHaveAttribute("data-style", "");
  await page.getByRole("button", { name: "Переключить на русский" }).click();
  await expect(page.locator("h1")).toHaveText("Совет агентов.");
});
