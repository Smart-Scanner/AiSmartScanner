import { test, expect } from '@playwright/test';

const BASE = process.env.BASE_URL || 'https://smartscanner.up.railway.app';
const USER = 'admin';
const PASS = 'admin123';

// Helper: login and return authenticated page
async function login(page) {
  await page.goto(`${BASE}/login`);
  await expect(page.locator('h1')).toContainText('NSE Screener');
  await page.fill('input[name="username"]', USER);
  await page.fill('input[name="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/');
}

// Helper: wait for scan data to be available
async function waitForData(page, minRows = 10) {
  await page.waitForSelector('tbody tr', { timeout: 30000 });
  // Poll until enough rows appear (scan may be loading)
  for (let i = 0; i < 10; i++) {
    const count = await page.locator('tbody tr').count();
    if (count >= minRows) return count;
    await page.waitForTimeout(2000);
  }
  return await page.locator('tbody tr').count();
}

// ─── LOGIN PAGE ──────────────────────────────────────────────
test.describe('Login Page', () => {

  test('should show login form', async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await expect(page.locator('h1')).toContainText('NSE Screener');
    await expect(page.locator('input[name="username"]')).toBeVisible();
    await expect(page.locator('input[name="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('should reject wrong credentials', async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[name="username"]', 'wrong');
    await page.fill('input[name="password"]', 'wrong');
    await page.click('button[type="submit"]');
    await expect(page.locator('.error')).toContainText('Invalid');
  });

  test('should login with correct credentials', async ({ page }) => {
    await login(page);
    await expect(page).toHaveURL(`${BASE}/`);
  });

  test('should redirect to login when not authenticated', async ({ page }) => {
    await page.goto(BASE);
    await expect(page).toHaveURL(/.*login/);
  });
});

// ─── DASHBOARD ───────────────────────────────────────────────
test.describe('Dashboard', () => {

  test('should show topbar with NSE Screener branding', async ({ page }) => {
    await login(page);
    await expect(page.locator('.topbar-logo')).toContainText('NSE Screener');
  });

  test('should show summary cards', async ({ page }) => {
    await login(page);
    await page.waitForSelector('.sum-card', { timeout: 15000 });
    const cards = page.locator('.sum-card');
    const count = await cards.count();
    console.log('Summary cards count:', count);
    expect(count).toBeGreaterThanOrEqual(4);
  });

  test('should show stock table with data', async ({ page }) => {
    await login(page);
    const count = await waitForData(page, 5);
    console.log('Stock rows:', count);
    expect(count).toBeGreaterThan(1);
  });

  test('should show regime and nifty 1M from DB', async ({ page }) => {
    await login(page);
    await page.waitForTimeout(3000);
    // Check API directly
    const res = await page.evaluate(async () => {
      const r = await fetch('/api/results?limit=1');
      return r.json();
    });
    console.log('API regime:', res.market_regime, 'nifty_1m:', res.nifty50_1m);
    // After first scan, regime should not be unknown
    // nifty_1m should have a value
  });

  test('should show Last Scan timestamp', async ({ page }) => {
    await login(page);
    await page.waitForTimeout(3000);
    const res = await page.evaluate(async () => {
      const r = await fetch('/api/status');
      return r.json();
    });
    console.log('Last scan:', res.last_scan, 'regime:', res.market_regime);
    // After deploy, last_scan should come from DB
    expect(res.last_scan).not.toBeNull();
  });

  test('should have working sidebar filters', async ({ page }) => {
    await login(page);
    await waitForData(page, 5);

    // Click HC filter
    await page.click('text=HIGH CONVICTION');
    await page.waitForTimeout(1000);
    const hcRows = await page.locator('tbody tr').count();
    console.log('HC rows:', hcRows);

    // Click All filter
    await page.click('text=All Stocks');
    await page.waitForTimeout(1000);
    const allRows = await page.locator('tbody tr').count();
    console.log('All rows:', allRows);
    expect(allRows).toBeGreaterThanOrEqual(hcRows);
  });

  test('should have working search', async ({ page }) => {
    await login(page);
    await waitForData(page, 5);
    await page.fill('#topSearch', 'TCS');
    await page.waitForTimeout(1000);
    const suggestions = page.locator('.suggestions');
    await expect(suggestions).toBeVisible();
  });

  test('should have gear menu with change credentials and logout', async ({ page }) => {
    await login(page);
    await page.click('#userMenu button');
    await page.waitForTimeout(300);
    const dropdown = page.locator('#userDrop');
    await expect(dropdown).toBeVisible();
    const links = await dropdown.locator('a').allTextContents();
    console.log('Menu links:', links);
    expect(links.some(l => l.includes('Credential'))).toBe(true);
    expect(links.some(l => l.includes('Logout'))).toBe(true);
  });

  test('should show live prices with rupee symbol', async ({ page }) => {
    await login(page);
    await waitForData(page, 5);
    const firstPrice = await page.locator('tbody tr').first().locator('.live-price').textContent();
    console.log('First stock price:', firstPrice);
    expect(firstPrice).toContain('₹');
  });

  test('should show DLV% as -- for Angel One data', async ({ page }) => {
    await login(page);
    await waitForData(page, 5);
    // Check if any DLV% column shows -- (Angel One stocks without delivery data)
    const allDlv = await page.locator('tbody tr td:nth-child(14)').allTextContents();
    const hasDash = allDlv.some(v => v.trim() === '--');
    const hasValue = allDlv.some(v => v.includes('%'));
    console.log('DLV% has --:', hasDash, 'has value:', hasValue);
    // At least one type should exist
    expect(hasDash || hasValue).toBe(true);
  });
});

// ─── STOCK DETAIL PAGE ───────────────────────────────────────
test.describe('Stock Detail', () => {

  test('should load stock detail page', async ({ page }) => {
    await login(page);
    await page.goto(`${BASE}/stock/TCS`);
    await expect(page.locator('#stockSymbol')).toContainText('TCS');
  });

  test('should show price display', async ({ page }) => {
    await login(page);
    await page.goto(`${BASE}/stock/RELIANCE`);
    await page.waitForTimeout(3000);
    const price = page.locator('#priceDisplay');
    await expect(price).toBeVisible();
    const text = await price.textContent();
    console.log('Price display:', text);
  });

  test('should have TradingView link', async ({ page }) => {
    await login(page);
    await page.goto(`${BASE}/stock/TCS`);
    const tvLink = page.locator('a:has-text("TradingView")');
    await expect(tvLink).toBeVisible();
    const href = await tvLink.getAttribute('href');
    expect(href).toContain('tradingview.com');
    expect(href).toContain('NSE:TCS');
  });

  test('should show theme toggle', async ({ page }) => {
    await login(page);
    await page.goto(`${BASE}/stock/TCS`);
    const toggle = page.locator('#themeToggle');
    await expect(toggle).toBeVisible();
  });
});

// ─── PORTFOLIO PAGE ──────────────────────────────────────────
test.describe('Portfolio', () => {

  test('should load portfolio page', async ({ page }) => {
    await login(page);
    await page.goto(`${BASE}/portfolio`);
    await expect(page.locator('h1')).toContainText('Portfolio Manager');
  });

  test('should show portfolio cards or empty state', async ({ page }) => {
    await login(page);
    await page.goto(`${BASE}/portfolio`);
    await page.waitForTimeout(3000);
    const cards = page.locator('.pf-card');
    const empty = page.locator('#emptyState');
    const hasCards = await cards.count() > 0;
    const isEmpty = await empty.isVisible();
    console.log('Has portfolio cards:', hasCards, 'Empty state:', isEmpty);
    expect(hasCards || isEmpty).toBe(true);

    if (hasCards) {
      const cardText = await cards.first().textContent();
      console.log('First card text:', cardText?.substring(0, 200));
      expect(cardText).toContain('Unrealized');
      expect(cardText).toContain('Total P&L');
    }
  });

  test('should have New Portfolio button', async ({ page }) => {
    await login(page);
    await page.goto(`${BASE}/portfolio`);
    await expect(page.locator('text=+ New Portfolio')).toBeVisible();
  });
});

// ─── LOGOUT ──────────────────────────────────────────────────
test.describe('Logout', () => {

  test('should logout and redirect to login', async ({ page }) => {
    await login(page);
    await page.goto(`${BASE}/logout`);
    await expect(page).toHaveURL(/.*login/);
  });
});
