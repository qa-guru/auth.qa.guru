#!/usr/bin/env node
// Account Console: selenoidToken is visible and persists. Restores the previous value.
//
//   node scripts/account-selenoid-token-check.mjs           # stand, student-demo
//   node scripts/account-selenoid-token-check.mjs --prod    # auth.qa.guru, staff-pilot

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const DEV_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const HOME = process.env.HOME || "";
const LIVE_STAFF = "svasenkov";
const PROBE = "selenoid-token-probe-ok1";

function loadKv(path) {
  const env = {};
  try {
    for (const line of readFileSync(path, "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
      const idx = trimmed.indexOf("=");
      env[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
    }
  } catch {
    /* missing file */
  }
  return env;
}

const prod = process.argv.includes("--prod");
const stand = loadKv(resolve(DEV_DIR, ".env"));
const prodEnv = loadKv(resolve(HOME, ".config/auth-qa-guru/keycloak.env"));
const pilots = loadKv(process.env.PILOT_ENV || resolve(HOME, ".config/auth-qa-guru/pilot.env"));

const BASE = prod
  ? (prodEnv.KC_HOSTNAME || "https://auth.qa.guru").replace(/\/$/, "")
  : `http://localhost:${stand.KEYCLOAK_HTTP_PORT ?? "8543"}`;
const REALM = process.env.REALM ?? "qaguru";
const USER = prod ? (pilots.PILOT_STAFF_USERNAME || "staff-pilot") : (process.env.STAND_USER || "student-demo");
const PASSWORD = prod ? pilots.PILOT_STAFF_PASSWORD : stand.STAND_USER_PASSWORD;
const ACCOUNT = `${BASE}/realms/${REALM}/account/`;

if (USER.toLowerCase() === LIVE_STAFF) {
  throw new Error("refusing: pilots are not svasenkov");
}
if (!PASSWORD) {
  throw new Error(prod ? "PILOT_STAFF_PASSWORD missing in pilot.env" : "STAND_USER_PASSWORD missing in dev/.env");
}

const steps = [];
const record = (name, ok, detail) => {
  steps.push({ step: name, ok, detail });
  console.error(`${ok ? "ok   " : "FAIL "} ${name}${detail ? ` — ${detail}` : ""}`);
};

async function login(page) {
  await page.goto(ACCOUNT, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#kc-form-login", { timeout: 20000 });
  await page.fill("#username", USER);
  await page.fill("#password", PASSWORD);
  await page.click("#kc-login");
  await page.waitForSelector("#selenoidToken", { timeout: 25000 });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1200 } });
    page.on("pageerror", (err) => errors.push(`pageerror ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(`console ${msg.text()}`);
    });
    await login(page);

    const field = page.locator("#selenoidToken");
    record("field visible", await field.isVisible());
    const label = ((await page.locator('label[for="selenoidToken"]').textContent()) || "").trim();
    record("label", label.includes("Токен Selenoid"), label);
    const body = await page.locator("body").innerText();
    record("group Selenoid", body.includes("Selenoid"));
    record(
      "hint",
      body.includes("Не короче 20") && body.includes("парол"),
      "length + not password",
    );
    record("not live staff", USER.toLowerCase() !== LIVE_STAFF, USER);

    const original = await field.inputValue();
    await field.fill(PROBE);
    await page.getByRole("button", { name: /Сохранить|Save/ }).click();
    await page.waitForFunction(
      () => /учетная запись была обновлена|account has been updated/i.test(document.body.innerText),
      { timeout: 15000 },
    );
    record("saved", true);

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#selenoidToken", { timeout: 25000 });
    const persisted = await page.locator("#selenoidToken").inputValue();
    record("persisted", persisted === PROBE, `len=${persisted.length}`);

    await page.locator("#selenoidToken").fill(original);
    await page.getByRole("button", { name: /Сохранить|Save/ }).click();
    await page.waitForFunction(
      () => /учетная запись была обновлена|account has been updated/i.test(document.body.innerText),
      { timeout: 15000 },
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#selenoidToken", { timeout: 25000 });
    const restored = await page.locator("#selenoidToken").inputValue();
    record("restored", restored === original, original ? "previous value" : "empty");
    record("no console error", errors.length === 0, errors.slice(0, 3).join("; "));
  } finally {
    await browser.close();
  }
  const failed = steps.filter((s) => !s.ok);
  console.log(JSON.stringify({ ok: failed.length === 0, base: BASE, user: USER, steps }, null, 2));
  if (failed.length) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
