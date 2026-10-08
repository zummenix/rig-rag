import { defineConfig, devices } from '@playwright/test';

import { HAPPY_BASE_URL } from './support';

// The mock servers and the WASM bundle are built in `global-setup.ts`; this
// config only points the browser at the happy server.
export default defineConfig({
  testDir: './specs',
  globalSetup: './global-setup.ts',
  // One worker: the streaming spec reads the answer as it arrives, and a single
  // browser keeps that timing predictable.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: HAPPY_BASE_URL,
    trace: 'retain-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
