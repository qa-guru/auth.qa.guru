// Stand proof: «Forgot password?» mails a reset for USERNAME (email is not a login),
// the link from Mailpit changes the password, the new password signs in.
//
//   node scripts/reset-password-check.mjs
//
// Restores STAND_USER_PASSWORD afterwards so passkey-login-check keeps working.

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
const BASE = `http://localhost:${env.KEYCLOAK_HTTP_PORT ?? "8543"}`;
const MAILPIT = `http://127.0.0.1:${env.MAILPIT_UI_PORT ?? "8025"}`;
const REALM = process.env.REALM ?? "qaguru";
const USER = process.env.STAND_USER ?? "student-demo";
const ORIGINAL = env.STAND_USER_PASSWORD;
const NEW_PASSWORD = "StandReset12xx";
const ACCOUNT = `${BASE}/realms/${REALM}/account/`;
const PKCE = "code_challenge_method=S256&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";
const authUrl =
  `${BASE}/realms/${REALM}/protocol/openid-connect/auth?client_id=account-console` +
  `&redirect_uri=${encodeURIComponent(ACCOUNT)}&response_type=code&scope=openid&${PKCE}`;

const steps = [];
const record = (name, ok, detail) => {
  steps.push({ step: name, ok, detail });
  console.error(`${ok ? "ok   " : "FAIL "} ${name}${detail ? ` — ${detail}` : ""}`);
};

const loggedIn = async (page) => (await page.locator("#kc-form-login").count()) === 0;

async function adminToken() {
  const form = new URLSearchParams({
    client_id: "admin-cli",
    grant_type: "password",
    username: env.KC_BOOTSTRAP_ADMIN_USERNAME,
    password: env.KC_BOOTSTRAP_ADMIN_PASSWORD,
  });
  const resp = await fetch(`${BASE}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: form,
  });
  if (!resp.ok) throw new Error(`admin token ${resp.status}`);
  return (await resp.json()).access_token;
}

async function restorePassword(token) {
  const auth = { Authorization: `Bearer ${token}` };
  const [user] = await (
    await fetch(`${BASE}/admin/realms/${REALM}/users?username=${USER}&exact=true`, { headers: auth })
  ).json();
  if (!user) throw new Error(`${USER} missing — run seed-stand-users.py`);
  const resp = await fetch(`${BASE}/admin/realms/${REALM}/users/${user.id}/reset-password`, {
    method: "PUT",
    headers: { ...auth, "Content-Type": "application/json" },
    body: JSON.stringify({ type: "password", value: ORIGINAL, temporary: false }),
  });
  if (!resp.ok) throw new Error(`restore password ${resp.status} ${await resp.text()}`);
}

async function mailpit(path, { method = "GET" } = {}) {
  const resp = await fetch(`${MAILPIT}${path}`, { method });
  if (!resp.ok) throw new Error(`mailpit ${method} ${path} -> ${resp.status}`);
  if (resp.status === 204) return null;
  const text = await resp.text();
  if (!text || text === "ok") return null;
  if (text.startsWith("{") || text.startsWith("[")) return JSON.parse(text);
  return null;
}

function rewriteLink(href) {
  return href
    .replace("://localhost:8080", "://localhost:8543")
    .replace("://keycloak:8080", "://localhost:8543")
    .replace("://127.0.0.1:8080", "://127.0.0.1:8543");
}

async function waitForMessage(afterCreated) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const list = await mailpit("/api/v1/messages");
    const hit = (list?.messages || []).find((m) => {
      if (afterCreated && m.Created && m.Created <= afterCreated) return false;
      const to = (m.To || []).map((t) => (t.Address || "").toLowerCase());
      return to.includes("demo@qa.guru") || (m.Subject || "").toLowerCase().includes("парол");
    });
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("no reset mail in Mailpit");
}

function extractActionLink(html, text) {
  const blob = `${html || ""}\n${text || ""}`;
  const match = blob.match(/https?:\/\/[^"'\\\s]+action-token[^"'\\\s]*/i);
  return match ? rewriteLink(match[0].replace(/&amp;/g, "&")) : "";
}

async function main() {
  if (!ORIGINAL) throw new Error("STAND_USER_PASSWORD missing in dev/.env");
  const token = await adminToken();

  await mailpit("/api/v1/messages", { method: "DELETE" });
  const empty = await mailpit("/api/v1/messages");
  record("mailpit empty", (empty?.messages || []).length === 0, `${empty?.messages?.length ?? "?"}`);

  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    await page.goto(authUrl, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#kc-form-login");
    const forgot = page.getByRole("link", { name: "Забыли пароль?" });
    record("forgot-password link on login", (await forgot.count()) === 1);

    await forgot.click();
    await page.waitForSelector("#username");
    record("reset form asks for username", (await page.locator("#username").count()) === 1);
    record(
      "reset form has no email field",
      (await page.locator("#email").count()) === 0,
      "loginWithEmail stays off",
    );

    const beforeEmail = await mailpit("/api/v1/messages");
    const beforeCount = beforeEmail?.messages?.length ?? 0;
    await page.fill("#username", "demo@qa.guru");
    await page.locator("#kc-form-buttons input[type=submit], #kc-form-buttons button, button[type=submit]").first().click();
    await page.waitForTimeout(1500);
    const afterEmailAsId = await mailpit("/api/v1/messages");
    record(
      "email is not a login — no mail for demo@qa.guru",
      (afterEmailAsId?.messages?.length ?? 0) === beforeCount,
      `${beforeCount} → ${afterEmailAsId?.messages?.length ?? "?"}`,
    );

    await page.goto(authUrl, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#kc-form-login");
    await page.getByRole("link", { name: "Забыли пароль?" }).click();
    await page.waitForSelector("#username");
    const marker = new Date().toISOString();
    await page.fill("#username", USER);
    await page.locator("#kc-form-buttons input[type=submit], #kc-form-buttons button, button[type=submit]").first().click();
    await page.waitForTimeout(500);

    const msg = await waitForMessage(marker);
    const body = await mailpit(`/api/v1/message/${msg.ID}`);
    const link = extractActionLink(body?.HTML, body?.Text);
    record("reset mail arrived for username", Boolean(link), msg.Subject || "");
    if (!link) throw new Error("reset mail had no action-token link");

    await page.goto(link, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#password-new, input[name=password-new]");
    await page.fill("#password-new, input[name=password-new]", NEW_PASSWORD);
    await page.fill("#password-confirm, input[name=password-confirm]", NEW_PASSWORD);
    await page.locator("#kc-form-buttons input[type=submit], #kc-form-buttons button, button[type=submit]").first().click();

    await page.goto("about:blank");
    await context.clearCookies();
    await page.goto(authUrl, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#kc-form-login");
    await page.fill("#username", USER);
    await page.fill("#password", NEW_PASSWORD);
    await page.click("#kc-login");
    await page.waitForURL((url) => url.href.startsWith(ACCOUNT), { timeout: 15000 });
    record("signed in with the new password", await loggedIn(page), USER);
  } finally {
    await browser.close();
    await restorePassword(token);
  }

  const ok = steps.every((s) => s.ok);
  console.log(JSON.stringify({ ok, realm: REALM, user: USER, steps }, null, 2));
  if (!ok) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
