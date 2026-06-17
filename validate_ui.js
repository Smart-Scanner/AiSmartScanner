const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();

  // Login first
  console.log('Logging in...');
  await page.goto('http://127.0.0.1:5051/login', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);
  
  // Fill login if needed
  const usernameField = await page.$('input[name="username"], input[type="text"]');
  if (usernameField) {
    await usernameField.fill('admin');
    const pwField = await page.$('input[name="password"], input[type="password"]');
    if (pwField) await pwField.fill('admin');
    const submitBtn = await page.$('button[type="submit"], input[type="submit"], .btn-submit, button:has-text("Launch")');
    if (submitBtn) await submitBtn.click();
    await page.waitForTimeout(3000);
  }

  const pages = [
    { name: 'dashboard', url: 'http://127.0.0.1:5051/' },
    { name: 'top_picks', url: 'http://127.0.0.1:5051/top-picks' },
    { name: 'high_conviction', url: 'http://127.0.0.1:5051/high-conviction' },
    { name: 'golden', url: 'http://127.0.0.1:5051/golden-setups' },
    { name: 'breakouts', url: 'http://127.0.0.1:5051/breakouts' },
    { name: 'outcome', url: 'http://127.0.0.1:5051/outcome-analytics' },
    { name: 'portfolio', url: 'http://127.0.0.1:5051/portfolio' },
    { name: 'mission_control', url: 'http://127.0.0.1:5051/mission-control' },
    { name: 'paper_trades', url: 'http://127.0.0.1:5051/paper-trades' },
  ];

  const outDir = 'screenshots_audit';
  const fs = require('fs');
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir);

  for (const item of pages) {
    try {
      console.log(`Navigating to ${item.name}...`);
      await page.goto(item.url, { waitUntil: 'networkidle', timeout: 15000 });
      await page.waitForTimeout(2000);

      // Dark Theme screenshot
      await page.screenshot({ path: `${outDir}/${item.name}_dark.png`, fullPage: true });

      // Switch to Light Theme
      await page.evaluate(() => {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('theme', 'light');
      });
      await page.waitForTimeout(1000);
      await page.screenshot({ path: `${outDir}/${item.name}_light.png`, fullPage: true });

      // Switch back to Dark
      await page.evaluate(() => {
        document.documentElement.setAttribute('data-theme', 'dark');
        localStorage.setItem('theme', 'dark');
      });
      await page.waitForTimeout(500);

      console.log(`✅ ${item.name} captured (dark + light)`);
    } catch (e) {
      console.error(`❌ ${item.name}: ${e.message}`);
    }
  }

  // Step 8: Overflow Validation
  console.log('\n=== Step 8: Horizontal Overflow Validation ===');
  const viewports = [375, 390, 414, 768];
  const overflowResults = {};

  for (const item of pages) {
    overflowResults[item.name] = {};
    for (const w of viewports) {
      try {
        await page.setViewportSize({ width: w, height: 812 });
        await page.goto(item.url, { waitUntil: 'networkidle', timeout: 15000 });
        await page.waitForTimeout(1000);

        const overflow = await page.evaluate(() => {
          return document.documentElement.scrollWidth - window.innerWidth;
        });

        const pass = overflow <= 2;
        overflowResults[item.name][w] = { overflow, pass };
        console.log(`  ${item.name} @ ${w}px: scrollWidth overflow = ${overflow}px ${pass ? '✅' : '❌'}`);
      } catch (e) {
        overflowResults[item.name][w] = { overflow: -1, pass: false, error: e.message };
        console.error(`  ${item.name} @ ${w}px: ERROR ${e.message}`);
      }
    }
  }

  // Write overflow results
  let report = '# Step 8: Horizontal Overflow Validation\n\n';
  report += '| Page | 375px | 390px | 414px | 768px |\n';
  report += '|------|-------|-------|-------|-------|\n';
  for (const [name, widths] of Object.entries(overflowResults)) {
    const cells = viewports.map(w => {
      const r = widths[w];
      return r.pass ? `✅ ${r.overflow}px` : `❌ ${r.overflow}px`;
    });
    report += `| ${name} | ${cells.join(' | ')} |\n`;
  }
  fs.writeFileSync(`${outDir}/overflow_validation.md`, report);

  await browser.close();
  console.log('\nDone! Screenshots saved to screenshots_audit/');
})();
