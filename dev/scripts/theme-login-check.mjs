#!/usr/bin/env node
// Login-theme check: qaguru CSS + form ids + one pilot (never svasenkov).
//
//   node scripts/theme-login-check.mjs              # stand, student-demo
//   node scripts/theme-login-check.mjs --prod       # auth.qa.guru, staff-pilot

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const DEV_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const HOME = process.env.HOME || "";
const LIVE_STAFF = "svasenkov";
const PKCE = "code_challenge_method=S256&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";

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
const authUrl =
  `${BASE}/realms/${REALM}/protocol/openid-connect/auth?client_id=account-console` +
  `&redirect_uri=${encodeURIComponent(ACCOUNT)}&response_type=code&scope=openid&${PKCE}`;

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

async function main() {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on("pageerror", (err) => errors.push(`pageerror ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(`console ${msg.text()}`);
    });
    const adminAuthUrl =
      `${BASE}/realms/master/protocol/openid-connect/auth?client_id=security-admin-console` +
      `&redirect_uri=${encodeURIComponent(`${BASE}/admin/master/console/`)}&response_type=code&scope=openid&${PKCE}`;
    if (prod) {
      const resp = await fetch(adminAuthUrl, { redirect: "manual" });
      record("admin closed from internet", resp.status === 403, `status=${resp.status}`);
    } else {
      await page.goto(adminAuthUrl, { waitUntil: "domcontentloaded" });
      await page.waitForSelector("#kc-form-login", { timeout: 15000 });
      const adminHrefs = await page.locator('link[rel="stylesheet"]').evaluateAll((els) =>
        els.map((el) => el.getAttribute("href") || "")
      );
      record(
        "admin stock css",
        adminHrefs.some((h) => h.includes("/login/keycloak.v2/")) && !adminHrefs.some((h) => h.includes("/login/qaguru/")),
        adminHrefs.filter((h) => h.includes("/login/")).join(" | "),
      );
      record("admin no ds header", (await page.locator('[data-testid="header"]').count()) === 0);
      record("admin form", (await page.locator("#username").count()) === 1 && (await page.locator("#kc-login").count()) === 1);
    }

    await page.goto(authUrl, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#kc-form-login", { timeout: 15000 });
    const hrefs = await page.locator('link[rel="stylesheet"]').evaluateAll((els) =>
      els.map((el) => el.getAttribute("href") || "")
    );
    const cssOk =
      hrefs.some((h) => h.includes("/login/qaguru/") && h.includes("login.css")) &&
      hrefs.some((h) => h.includes("header.css")) &&
      hrefs.some((h) => h.includes("shell.css"));
    record("qaguru css", cssOk, hrefs.filter((h) => h.includes("login")).join(" | "));
    record("form", (await page.locator("#username").count()) === 1 && (await page.locator("#password").count()) === 1);
    record("submit", (await page.locator("#kc-login").count()) === 1);
    record("passkey", (await page.locator("#authenticateWebAuthnButton").count()) === 1);
    record("github", (await page.locator("#social-github").count()) === 1);
    record("ds header", (await page.locator('[data-testid="header"]').count()) === 1);
    record("ds brand", (await page.locator('[data-testid="header-brand-link"]').count()) === 1);
    const layout = await page.evaluate(() => {
      const header = document.querySelector('[data-testid="header"]');
      const user = document.getElementById("username");
      const signin = document.getElementById("kc-login");
      const githubOpen = document.querySelector('a[data-testid="header-github"]');
      return {
        headerH: header ? header.getBoundingClientRect().height : 0,
        userW: user ? user.getBoundingClientRect().width : 0,
        signinW: signin ? signin.getBoundingClientRect().width : 0,
        githubClosed: !githubOpen || githubOpen.querySelector("#kc-form-login") != null,
      };
    });
    record("header height", layout.headerH > 32 && layout.headerH <= 48, `h=${layout.headerH.toFixed(1)}px`);
    record("username width", layout.userW >= 200, `w=${layout.userW.toFixed(1)}px`);
    record("signin width", layout.signinW >= 200, `w=${layout.signinW.toFixed(1)}px`);
    record("github a closed", !layout.githubClosed);
    const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    record("dark surface", bg === "rgb(21, 20, 20)", bg);
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      const stack = await page.evaluate(() => {
        const signin = document.getElementById("kc-login");
        const passkey = document.getElementById("authenticateWebAuthnButton");
        if (!signin || !passkey) return { gap: -1, overlap: true };
        const a = signin.getBoundingClientRect();
        const b = passkey.getBoundingClientRect();
        return { gap: b.top - a.bottom, overlap: b.top < a.bottom - 0.5 };
      });
      record(`signin/passkey gap ${width}`, !stack.overlap && stack.gap >= 8, `gap=${stack.gap.toFixed(1)}px`);
    }

    await page.fill("#username", USER);
    await page.fill("#password", PASSWORD);
    await page.click("#kc-login");
    await page.waitForFunction(() => !document.querySelector("#kc-form-login"), { timeout: 20000 });
    const landed = page.url();
    const stillForm = (await page.locator("#kc-form-login").count()) === 0;
    record("pilot login", stillForm && !landed.includes("error"), landed.split("?")[0]);
    record("not live staff", USER !== LIVE_STAFF, USER);
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
