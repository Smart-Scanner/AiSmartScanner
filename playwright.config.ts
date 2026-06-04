import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60000,
  use: {
    baseURL: process.env.BASE_URL || 'https://smartscanner.up.railway.app',
    headless: process.env.HEADLESS !== 'false',
    screenshot: 'on',
  },
});
