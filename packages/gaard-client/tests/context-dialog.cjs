// Run with Playwright installed and GAARD_CLIENT_TEST_URL pointing to a local client.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.GAARD_TEST_BROWSER_CHANNEL || undefined,
  });
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      let calls = 0;
      let fail = false;
      let holdResponse;
      await page.route("https://cdn.jsdelivr.net/**", route => route.fulfill({status: 200, body: ""}));
      await page.addInitScript(() => {
        localStorage.setItem("gaard_client_token", "test-token");
        localStorage.setItem("gaard_client_username", "tester");
        localStorage.setItem("gaard_client_enterprise_access", "true");
        localStorage.setItem("gaard_client_active_conversation_id", "chat");
      });
      const turns = Array.from({length: 12}, (_, i) => ({
        id: "turn-" + i, conversation_id: "chat", mode: "sql", status: "completed",
        question: "How many active patients in period " + i + "?",
        answer: "There were 125 active patients.", sql: "SELECT COUNT(*) FROM patients",
        metadata: {duration_ms: 221, datasource_id: "medical", output_classification: "neutral_data"},
      }));
      const snapshot = "Count active patients admitted in May in Warsaw, including only completed visits.";
      await page.route("**/api/**", async route => {
        const path = new URL(route.request().url()).pathname;
        let payload = {};
        if (path.endsWith("/context")) {
          calls++;
          if (holdResponse) await holdResponse;
          if (fail) return route.fulfill({status: 503, json: {detail: "Temporarily unavailable"}});
          payload = {context: snapshot};
        } else if (path === "/api/conversations") {
          payload = {items: [{id:"chat",title:"Patients",turn_count:12}]};
        } else if (path === "/api/conversations/chat") {
          payload = {item:{id:"chat",title:"Patients"},turns};
        } else if (path.endsWith("/me")) {
          payload = {username:"tester",enterprise_access:true,must_change_password:false};
        } else {
          payload = {items:[],enterprise_access:true};
        }
        return route.fulfill({status: 200, json: payload});
      });
      await page.goto(process.env.GAARD_CLIENT_TEST_URL || "http://127.0.0.1:8014/");
      await page.getByRole("button", {name:"kontekst",exact:true}).last().waitFor();
      const history = page.locator(".history");
      await history.evaluate(el => {el.scrollTop = el.scrollHeight;});
      const pageWidthBefore = await page.evaluate(() => document.documentElement.scrollWidth);
      const scrollBefore = await history.evaluate(el => el.scrollTop);
      await page.getByRole("button", {name:"kontekst",exact:true}).last().click();
      const dialog = page.getByRole("dialog", {name:"Query context"});
      await dialog.locator("textarea").waitFor();
      assert.equal(await dialog.locator("textarea").inputValue(), snapshot);
      assert.equal(await dialog.locator("textarea").getAttribute("readonly"), "");
      assert.equal(calls, 1);
      const box = await dialog.boundingBox();
      assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1);
      if (process.env.GAARD_SCREENSHOT_DIR) {
        await page.screenshot({path: process.env.GAARD_SCREENSHOT_DIR + "/gaard-context-" + viewport.width + ".png"});
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), pageWidthBefore);
      assert.equal(await dialog.evaluate(el => el.scrollWidth > el.clientWidth), false);
      await page.keyboard.press("Escape");
      await dialog.waitFor({state:"detached"});
      assert(Math.abs(await history.evaluate(el => el.scrollTop) - scrollBefore) < 2);
      fail = true;
      await page.getByRole("button", {name:"kontekst",exact:true}).last().click();
      await page.getByRole("button", {name:"Retry",exact:true}).waitFor();
      fail = false;
      await page.getByRole("button", {name:"Retry",exact:true}).click();
      await dialog.locator("textarea").waitFor();
      await page.getByRole("button", {name:"Close",exact:true}).click();
      let release;
      holdResponse = new Promise(resolve => {release = resolve;});
      await page.getByRole("button", {name:"kontekst",exact:true}).last().click();
      await page.getByRole("button", {name:"Close",exact:true}).click();
      release();
      await page.waitForTimeout(100);
      assert.equal(await page.getByRole("dialog").count(), 0);
      assert.deepEqual(errors, []);
      console.log("PASS context dialog, retry, cancellation, scroll, viewport " + viewport.width);
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
