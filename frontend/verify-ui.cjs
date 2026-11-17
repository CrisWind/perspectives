// Run against the local front end and backend. Uses a fresh browser profile.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.UI_URL || 'http://127.0.0.1:5173';
const apiBase = process.env.API_URL || 'http://127.0.0.1:8000';
const out = path.resolve(__dirname, '../docs/ui-design-v1/production');
fs.mkdirSync(out, {recursive:true});
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const errors = [], checks = [];
    page.on('pageerror', error => errors.push(error.message));
    async function bounds(selector) {
      const r = await page.locator(selector).boundingBox();
      const v = page.viewportSize();
      assert(r && r.x >= -1 && r.y >= -1 && r.x + r.width <= v.width + 1 && r.y + r.height <= v.height + 1, `${selector} exceeds viewport: ${JSON.stringify(r)}`);
    }
    async function checkMeetingConsole() {
      assert(await page.locator('.meeting-scene').evaluate(scene => {
        const art = scene.querySelector('.theater').getBoundingClientRect();
        const panel = scene.querySelector('.meeting-console').getBoundingClientRect();
        const frame = scene.getBoundingClientRect();
        const controls = [...scene.querySelectorAll('.stage-caption,.stage-actions,.table-controls')].filter(e => e.getClientRects().length);
        return Math.abs(panel.top - (art.top + art.height * .8)) < 3 &&
          controls.every(e => { const r = e.getBoundingClientRect(); return r.left >= frame.left && r.right <= frame.right && r.bottom <= frame.bottom; });
      }), 'Conversation must cover the lower scene and keep controls inside the frame');
    }
    for (const width of [1440,768,390,360]) {
      await page.setViewportSize({width,height:width === 1440 ? 1000 : 844});
      for (const route of ['/', '/choose/', '/roundtable/']) {
        await page.goto(base + route);
        await page.evaluate(() => document.fonts.ready);
        await page.waitForFunction(() => [...document.images].every(i => !i.getAttribute('src') || i.complete));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${route} overflows at ${width}`);
        assert.equal(await page.locator('[id$="role-info"],#personality-details,#personality-description,#role-question').count(), 0);
        assert.deepEqual(await page.evaluate(() => [...document.images].filter(i => i.getAttribute('src') && !i.naturalWidth).map(i => i.src)), []);
        if (route === '/') {
          assert.equal(await page.locator('.orbit-personality').count(),16);
          assert(await page.evaluate(() => {
            const images = [...document.querySelectorAll('.hero-figure img')];
            const animations = images.flatMap(i => i.getAnimations());
            return Array.from({length:9}, (_, n) => {
              animations.forEach(a => { a.pause(); a.currentTime = Number(a.effect.getTiming().duration) * n / 8; });
              const boxes = images.map(i => i.getBoundingClientRect()).sort((a,b) => a.left-b.left);
              return boxes.every((r,i) => r.left>=0 && r.right<=innerWidth && (!i || r.left>boxes[i-1].right));
            }).every(Boolean);
          }), 'Hero characters overlap');
          await page.locator('#orbit-pause').click();
          await page.locator('.orbit-personality').first().click();
          await bounds('#personality-dialog');
          await page.keyboard.press('Escape');
          await page.locator('.hero-actions [data-open-sample]').click();
          await bounds('#sample-dialog');
          const sample = page.frameLocator('#sample-frame');
          await sample.locator('#sample-position').fill('2');
          assert.match(await sample.locator('#speaker-name').textContent(), /晴/);
          await sample.locator('#sample-reset').click();
          await sample.locator('#sample-play').click();
          assert.equal(await sample.locator('#sample-play').getAttribute('aria-label'),'暂停样例');
          await sample.locator('#sample-play').press('Escape');
          await page.waitForFunction(() => !document.getElementById('sample-dialog').open);
          await page.waitForFunction(() => document.getElementById('sample-frame').getAttribute('src') === 'about:blank');
          assert.equal(await page.locator('#sample-frame').getAttribute('src'),'about:blank');
        } else if (route === '/choose/') {
          assert(await page.locator('#enter').isDisabled());
          await page.locator('#main-card').click();
          await bounds('#personality-dialog');
          assert.equal(await page.locator('#personality-dialog p,#personality-dialog details').count(),0);
          await page.keyboard.press('Escape');
          assert(await page.locator('#main-card').evaluate(e => e === document.activeElement));
          await page.locator('#next').click();
          assert.equal(await page.locator('#counter').textContent(),'02 / 16');
          await page.locator('#previous').click();
        } else {
          await page.locator('.sidebar-nav').waitFor({state:'visible'});
          assert.equal(await page.locator('body').evaluate(e => getComputedStyle(e).backgroundColor),'rgb(247, 248, 250)');
          assert.equal(await page.locator('.sidebar-nav').evaluate(e => getComputedStyle(e).backgroundColor),'rgb(255, 254, 250)');
          await bounds('.sidebar-nav');
          assert.equal(await page.locator('.stage-actions .paper-action').count(),1);
          await checkMeetingConsole();
          for (const id of ['brief','debate','result']) {
            await page.locator(`[data-panel="${id}"]`).click();
            await page.waitForTimeout(450);
            await bounds('#' + id);
            const r = await page.locator('#' + id).boundingBox();
            assert(Math.abs(r.x + r.width/2 - width/2)<2, `${id} not centered`);
            assert.equal(await page.locator('#'+id).evaluate(e => getComputedStyle(e).backgroundColor),'rgb(255, 254, 250)');
            if ([1440,390].includes(width)) await page.screenshot({path:path.join(out,`meeting-${id}-${width}.png`),fullPage:true});
            await page.keyboard.press('Escape');
            await page.waitForFunction(id => document.getElementById(id).hidden, id);
          }
        }
        checks.push({route,width,passed:true});
        if ([1440,390].includes(width)) await page.screenshot({path:path.join(out,`${route==='/'?'home':route.split('/')[1]}-${width}.png`),fullPage:true});
      }
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(base + '/choose/');
    await page.locator('#question').fill('是否先尝试副业再调整工作方向？');
    await page.locator('#leaning').fill('先保持稳定，做小范围尝试');
    await page.locator('#reasons').fill('希望兼顾收入与兴趣\n需要时间验证');
    for (const name of ['弈','溯','逆']) await page.getByRole('button',{name:'移出'+name,exact:true}).click();
    assert(await page.locator('#enter').isDisabled());
    await page.locator('#select-role').click();
    assert(!(await page.locator('#enter').isDisabled()));
    for (let i=0;i<16;i++) await page.locator('#next').click();
    assert.equal(await page.locator('#counter').textContent(),'01 / 16');
    await page.reload();
    assert.equal(await page.locator('#question').inputValue(),'是否先尝试副业再调整工作方向？');
    assert.equal(await page.locator('#seats .seat').count(),2);
    await page.getByRole('link',{name:'返回首页',exact:false}).click();
    await page.getByRole('link',{name:'Meet Your Council',exact:false}).click();
    assert.equal(await page.locator('#leaning').inputValue(),'先保持稳定，做小范围尝试');
    await page.locator('#enter').click();
    await page.waitForURL('**/roundtable/**', {timeout:70000});
    await page.waitForFunction(() => document.querySelectorAll('#cast .actor').length === 2);
    await page.waitForFunction(() => document.querySelectorAll('#feed .speech').length > 0, {timeout:60000});
    await page.locator('.stage-actions').getByRole('button',{name:'读完了',exact:true}).click();
    await page.locator('.stage-actions .paper-action').click();
    await page.locator('#reply').waitFor({state:'visible'});
    assert(!(await page.locator('#reply').isDisabled()));
    await checkMeetingConsole();
    await page.screenshot({path:path.join(out,'meeting-console-expanded-1440.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await checkMeetingConsole();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({path:path.join(out,'meeting-console-expanded-390.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('.stage-actions .paper-action').click();
    const sessionId = new URL(page.url()).searchParams.get('session');
    const history = await page.request.get(apiBase+'/api/sessions/'+sessionId+'/events?format=json').then(r=>r.json());
    assert.deepEqual(history.events.find(e=>e.type==='team.selected').data.team.map(p=>p.id),['nuannuan','laomou']);
    await page.reload();
    await page.waitForFunction(() => document.querySelectorAll('#cast .actor').length === 2);
    assert.equal(await page.locator('#dilemma').inputValue(),'是否先尝试副业再调整工作方向？');
    await page.getByRole('link',{name:'调整问题与阵容',exact:false}).click();
    assert.equal(await page.locator('#seats .seat').count(),2);
    await page.goBack();
    assert(new URL(page.url()).pathname.startsWith('/roundtable'));
    await page.goForward();
    assert.equal(await page.locator('#seats .seat').count(),2);
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.goto(base+'/');
    assert.equal(await page.locator('#orbit-pause').getAttribute('aria-pressed'),'true');
    assert(await page.locator('.hero-figure img').evaluateAll(images=>images.every(i=>getComputedStyle(i).animationName==='none')));
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify({checks,interactions:['image-only enlargement and keyboard dismissal','hero no-overlap at nine animation phases','sample seek/play/reset/close','card stack wraparound','2–4 guest validation','draft refresh and navigation','live API team selection and meeting restore','persistent sidebar and centered panels','history back/forward','reduced motion'],errors,sessionId},null,2));
    console.log(JSON.stringify({layouts:checks.length,errors:errors.length,sessionId,result:'passed'}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
