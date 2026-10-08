import path from "node:path";

import { loadEnvConfig } from "@next/env";
import { defineConfig, devices } from "@playwright/test";

// Same single source of config as the apps: the repo-root .env.
loadEnvConfig(path.resolve(__dirname, "../.."), true);

const WEB_URL = process.env.E2E_WEB_URL ?? "http://localhost:3000";
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false, // tests share the seeded demo organization
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "uv run uvicorn app.main:app --port 8000",
      cwd: path.resolve(__dirname, "../api"),
      url: `${API_URL}/health`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: "pnpm dev",
      cwd: __dirname,
      url: `${WEB_URL}/login`,
      reuseExistingServer: true,
      timeout: 180_000,
    },
  ],
});
