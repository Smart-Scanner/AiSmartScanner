const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 }
  });

  const urls = [
    { name: 'dashboard', url: 'http://127.0.0.1:5051/' },
    { name: 'top_picks', url: 'http://127.0.0.1:5051/top-picks' },
    { name: 'portfolio', url: 'http://127.0.0.1:5051/portfolio' },
    { name: 'mission_control', url: 'http://127.0.0.1:5051/mission-control' }
  ];

  for (const item of urls) {
    try {
      console.log(`Navigating to ${item.name}...`);
      await page.goto(item.url, { waitUntil: 'networkidle' });
      
      // Wait a bit extra for dynamic content
      await page.waitForTimeout(2000);

      // Dark Theme (default)
      await page.screenshot({ path: `screenshot_${item.name}_dark.png`, fullPage: true });

      // Switch to Light Theme
      console.log(`Switching to light theme for ${item.name}...`);
      // Most themes toggle by adding data-theme="light" to html or body, or a localstorage
      await page.evaluate(() => {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('theme', 'light');
        // trigger theme change event if any
        window.dispatchEvent(new Event('theme-change'));
      });
      await page.waitForTimeout(1000);
      
      await page.screenshot({ path: `screenshot_${item.name}_light.png`, fullPage: true });

      // Switch back to Dark Theme
      await page.evaluate(() => {
        document.documentElement.setAttribute('data-theme', 'dark');
        localStorage.setItem('theme', 'dark');
      });

    } catch (e) {
      console.error(`Error on ${item.name}: ${e.message}`);
    }
  }

  await browser.close();
})();
