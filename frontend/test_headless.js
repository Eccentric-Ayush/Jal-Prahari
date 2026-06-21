import puppeteer from 'puppeteer';

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();

  page.on('console', msg => console.log('BROWSER CONSOLE:', msg.text()));
  page.on('request', request => {
    if (request.url().includes('predict')) {
      console.log('BROWSER REQUEST:', request.url());
    }
  });
  page.on('requestfailed', request => {
    if (request.url().includes('predict')) {
      console.log('BROWSER REQUEST FAILED:', request.url(), request.failure().errorText);
    }
  });

  console.log('Navigating to http://localhost:5173 ...');
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle0' });
  
  await new Promise(r => setTimeout(r, 6000)); // wait for 6 seconds to see polling
  
  await browser.close();
})();
