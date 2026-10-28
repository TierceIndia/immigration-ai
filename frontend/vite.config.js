import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.js",
    // Playwright's suite lives in e2e/ and must not be collected by vitest.
    include: ["src/**/*.test.{js,jsx}"],
  },
});
