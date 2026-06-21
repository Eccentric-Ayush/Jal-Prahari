import puppeteer from 'puppeteer';

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();

  page.on('console', msg => console.log('BROWSER CONSOLE:', msg.text()));
  page.on('request', request => {
    if (request.url().includes('api/predict/risk') || request.url().includes('api/sensors')) {
      console.log('BROWSER REQUEST:', request.url());
    }
  });

  console.log('Navigating to http://localhost:5173 ...');
  // Capture all console errors and uncaught exceptions
  page.on('console', msg => {
    if (msg.type() === 'error') {
      console.log(`BROWSER ERROR: ${msg.text()}`);
    }
  });
  page.on('pageerror', error => {
    console.log(`UNCAUGHT EXCEPTION: ${error.message}`);
  });

  await page.goto('http://localhost:5173', { waitUntil: 'load', timeout: 15000 });

  // Wait 4 seconds to let map and initial predict/risk load
  await new Promise(r => setTimeout(r, 4000));

  // Take screenshot of initial state (before click)
  await page.screenshot({ path: 'screenshot_initial.png', fullPage: false });
  
  // Simulate clicking a sensor feature
  console.log('Simulating map click on a risk-circles feature...');
  await page.evaluate(() => {
    // We can't easily click a WebGL pixel, but we can trigger the 'click' event 
    // manually on the map layer if we have access to the map instance.
    // However, map is hidden in MapContainer's closure.
    // Let's just click the first WarningBanner in the sidebar instead!
    const banner = document.querySelector('.warning-banner');
    if (banner) {
      console.log('Found warning banner, clicking it to select sensor...');
      banner.click();
    } else {
      console.log('No warning banner found to click.');
    }
  });

  // Wait 5 seconds to let the history fetch complete and chart render
  await new Promise(r => setTimeout(r, 5000));

  // Take screenshot of post-click state
  await page.screenshot({ path: 'screenshot_after_click.png', fullPage: false });

  await page.screenshot({ path: 'dashboard_screenshot.png' });
  const hasChart = await page.evaluate(() => {
    return !!document.querySelector('.time-series-chart');
  });
  console.log('Is TimeSeriesChart rendered in DOM?', hasChart);

  await browser.close();
})();
