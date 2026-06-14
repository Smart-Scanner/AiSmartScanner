import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 120000,
  fullyParallel: true,
  reporter: [['json', { outputFile: 'test-results/audit-report.json' }]],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:5051',
    headless: process.env.HEADLESS !== 'false',
    screenshot: 'on',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'Desktop Chrome',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 720 },
      },
    },
    {
      name: 'Tablet Chrome',
      use: {
        ...devices['iPad (gen 7)'], // roughly 768x1024
        viewport: { width: 768, height: 1024 },
      },
    },
    {
      name: 'Mobile Chrome',
      use: {
        ...devices['Pixel 5'], // 393x851 or similar, we override viewport
        viewport: { width: 375, height: 667 },
        isMobile: true,
      },
    },
  ],
});
