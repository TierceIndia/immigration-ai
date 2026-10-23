import { defineConfig, devices } from "@playwright/test";

// End-to-end tests for the admin UI. They run against a production build of
// the app with every API call mocked in the browser, so they need no backend,
// no database and no live government site. Set
// PLAYWRIGHT_CHROMIUM_EXECUTABLE to reuse an already-installed Chromium.
const PORT = 4173;
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
    ...(executablePath ? { launchOptions: { executablePath } } : {}),
  },
  webServer: {
    // Bind explicitly to IPv4: Vite's default "localhost" can end up on ::1
    // only, which the baseURL above never reaches.
    command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: { VITE_API_BASE_URL: "http://127.0.0.1:9" },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
