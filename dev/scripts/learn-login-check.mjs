// P9: OIDC login on the learn stub for staff / mentor / student + no console error.
//
//   node scripts/learn-login-check.mjs

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const DEV_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function loadEnv() {
  const env = {};
  for (const line of readFileSync(resolve(DEV_DIR, ".env"), "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const idx = trimmed.indexOf("=");
    env[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return env;
}

const env = loadEnv();
const STUB = `http://127.0.0.1:${env.LEARN_STUB_PORT ?? "3040"}`;
const PASSWORD = env.STAND_USER_PASSWORD;
const PILOTS = [
  { kind: "staff", user: "svasenkov", role: "staff", view: "#view-staff", hidden: "#view-student" },
  { kind: "mentors", user: "mentor-demo", role: "mentor", view: "#view-mentor", hidden: "#view-staff" },
  { kind: "students", user: "student-demo", role: "student", view: "#view-student", hidden: "#view-staff" },
];

const steps = [];
const record = (name, ok, detail) => {
  steps.push({ step: name, ok, detail });
  console.error(`${ok ? "ok   " : "FAIL "} ${name}${detail ? ` — ${detail}` : ""}`);
};

async function main() {
  if (!PASSWORD) throw new Error("STAND_USER_PASSWORD missing in dev/.env");
  const browser = await chromium.launch();
  const errors = [];

  try {
    const landing = await browser.newContext();
    const landPage = await landing.newPage();
    landPage.on("pageerror", (err) => errors.push(`pageerror ${err.message}`));
    landPage.on("console", (msg) => {
      if (msg.type() === "error") errors.push(`console ${msg.text()}`);
    });
    await landPage.goto(STUB, { waitUntil: "domcontentloaded" });
    record("landing", (await landPage.locator("#login-oidc").count()) === 1, landPage.url());
    await landing.close();

    const seen = {};
    for (const pilot of PILOTS) {
      const context = await browser.newContext();
      const page = await context.newPage();
      page.on("pageerror", (err) => errors.push(`${pilot.kind} pageerror ${err.message}`));
      page.on("console", (msg) => {
        if (msg.type() === "error") errors.push(`${pilot.kind} console ${msg.text()}`);
      });
      await page.goto(STUB, { waitUntil: "domcontentloaded" });
      await page.click("#login-oidc");
      await page.waitForSelector("#kc-form-login", { timeout: 15000 });
      await page.fill("#username", pilot.user);
      await page.fill("#password", PASSWORD);
      await page.click("#kc-login");
      const who = page.locator("#who");
      await who.waitFor({ timeout: 15000 });
      const role = await who.getAttribute("data-role");
      const user = await who.getAttribute("data-username");
      const auth = await who.getAttribute("data-auth");
      const viewOk = (await page.locator(pilot.view).count()) === 1;
      const hiddenOk = (await page.locator(pilot.hidden).count()) === 0;
      const ok = user === pilot.user && role === pilot.role && auth === "oidc" && viewOk && hiddenOk;
      seen[pilot.kind] = role;
      record(
        `oidc ${pilot.kind}`,
        ok,
        `${user} role=${role} auth=${auth} view=${viewOk} hidden=${hiddenOk}`,
      );
      await context.close();
    }
    const distinct = new Set(Object.values(seen).filter(Boolean)).size;
    record("roles differ", distinct >= 2, JSON.stringify(seen));
    record("no console error", errors.length === 0, errors.join("; "));
  } finally {
    await browser.close();
  }

  const failed = steps.filter((s) => !s.ok);
  console.error(`\n${steps.length - failed.length}/${steps.length} checks passed`);
  if (failed.length) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
