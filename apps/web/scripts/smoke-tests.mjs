// Browser-level smoke test against the wrangler dev rehearsal (same workerd
// runtime as the deployment uses). Uses the system Edge install.
import { chromium } from "playwright-core";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:8787/";
const checks = [];
function check(name, ok, detail = "") {
  checks.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  headless: true,
});
try {
  const page = await browser.newPage();
  page.on("pageerror", (error) => check("no page errors", false, error.message));
  await page.goto(BASE, { waitUntil: "load", timeout: 60_000 });

  // The sample analyses automatically; wait for the vessel metric to fill.
  await page.waitForFunction(
    () => [...document.querySelectorAll(".metric-grid article strong")].some((el) => el.textContent === "6"),
    null,
    { timeout: 120_000 },
  );
  check("sample analysed automatically (6 vessels)", true);

  const metrics = await page.locator(".metric-grid article").allTextContents();
  check("movement segments = 80", metrics.some((m) => m.includes("Movement segments") && m.includes("80")));
  check("stops detected = 152", metrics.some((m) => m.includes("Stops detected") && m.includes("152")));
  check("landing candidates = 4", metrics.some((m) => m.includes("Landing candidates") && m.includes("4")));
  check("distance excludes flagged jumps (272.7 km)", metrics.some((m) => m.includes("272.7 km")));

  const quality = await page.locator(".quality-summary").textContent();
  check("counts show 992 rows · 0 rejected · 2 flagged", /992 rows · 0 rejected · 2 flagged/.test(quality.replace(/\s+/g, " ")), quality.replace(/\s+/g, " "));

  await page.click(".quality-summary a, a[href='#quality']");
  check("warning entries listed", (await page.locator("#quality table tbody tr, #quality ul li, #quality [class*=issue]").count()) > 0 || (await page.locator("#quality").textContent()).includes("65"));
  check("Unverified pill on landing candidates", (await page.locator(".unverified-pill").count()) === 4);

  const canvas = page.locator(".map-canvas canvas");
  await canvas.first().waitFor({ timeout: 30_000 });
  check("map rendered", (await canvas.count()) > 0);

  check("method note reflects settings", (await page.locator(".evidence-note").textContent()).includes("40 kn"));
  check("demo identity shown (no hardcoded user)", (await page.locator(".user").textContent()).includes("Demo workspace"));
  await page.screenshot({ path: "smoke-test.png", fullPage: true });
  console.log("screenshot: apps/web/smoke-test.png");
} finally {
  await browser.close();
  const failed = checks.filter((c) => !c.ok);
  console.log(`${checks.length} checks, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
}
