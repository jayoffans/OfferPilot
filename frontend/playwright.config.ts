import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 12_000 },
  reporter: "list",
  outputDir: "./artifacts/playwright",
  use: {
    baseURL: `http://127.0.0.1:${process.env.OFFERPILOT_E2E_WEB_PORT ?? 3001}`,
    ...devices["Desktop Chrome"],
    // Auth forms contain secrets; capture only explicitly chosen clean pages.
    trace: "off", screenshot: "off", video: "off",
  },
});
