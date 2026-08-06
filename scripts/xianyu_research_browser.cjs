#!/usr/bin/env node
/**
 * 闲鱼热销调研 · 浏览器降级方案（API 直连被滑块风控时用）。
 *
 * 原理：headful Chromium + 注入 XianyuPub 账号 cookie，打开闲鱼 PC 搜索页，
 * 逐页点击分页右箭头，拦截 mtop 搜索响应，按「人想要」排序输出榜单。
 * 浏览器有完整 JS 指纹（bx-ua），风控判定宽松，API 被 RGV587 拦时可兜底。
 *
 * 前置：
 *   1. DISPLAY 可用（headful 需要 X server；无则 xvfb-run）
 *   2. Playwright 浏览器：/home/taikoto/.cache/ms-playwright/chromium-1234/...（或用 playwright 自带）
 *   3. cookie 文件：见 xianyu_cookie_export.py（容器内导出）
 *
 * 用法：
 *   node xianyu_research_browser.mjs "<关键词>" <页数> --cookie /tmp/xianyu_cookie.txt --out /tmp/xr_<kw>
 *
 * 产物：--out/items.ndjson（含 item_id/title/price/want/seller/area/publishTime/image）
 * 排序榜单：用 xianyu_research.py 的 --from-ndjson 离线生成 topN.md/csv。
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const KEYWORD = args[0] || 'AI';
const PAGES = parseInt(args[1] || '12', 10);
const optIdx = args.indexOf('--cookie');
const COOKIE_FILE = optIdx > -1 ? args[optIdx + 1] : '/tmp/xianyu_cookie.txt';
const optOut = args.indexOf('--out');
const OUT_DIR = optOut > -1 ? args[optOut + 1] : `/tmp/xr_${KEYWORD}`;
const OUT_FILE = path.join(OUT_DIR, 'items.ndjson');

const cookieStr = fs.readFileSync(COOKIE_FILE, 'utf8').trim();
const cookies = cookieStr.split(';').map(p => {
  const i = p.indexOf('=');
  return { name: p.slice(0, i).trim(), value: p.slice(i + 1).trim(), domain: '.goofish.com', path: '/' };
});

const wantRe = /(\d+)\s*人想要/g;

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({
    headless: false, // headful 指纹更接近真人
    executablePath: '/home/taikoto/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome',
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox']
  });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    locale: 'zh-CN'
  });
  await ctx.addCookies(cookies);
  const page = await ctx.newPage();

  let seen = new Set();
  if (fs.existsSync(OUT_FILE)) {
    for (const line of fs.readFileSync(OUT_FILE, 'utf8').split('\n')) {
      try { seen.add(JSON.parse(line).item_id); } catch (e) {}
    }
  }
  console.log(`[start] kw=${KEYWORD} pages=${PAGES} already=${seen.size} → ${OUT_FILE}`);

  page.on('response', async (resp) => {
    const url = resp.url();
    if (!url.includes('mtop.taobao.idlemtopsearch.pc.search/1.0')) return;
    try {
      const body = await resp.text();
      if (!body.trim().startsWith('{')) return; // 登录/验证页 HTML
      const d = JSON.parse(body).data || {};
      const rl = d.resultList || [];
      if (!rl.length) return;
      let lines = [];
      for (const x of rl) {
        const it = x.data?.item?.main || {};
        const args = it.clickParam?.args || {};
        const dp = it.exContent?.detailParams || {};
        const s = JSON.stringify(it);
        wantRe.lastIndex = 0;
        let want = 0, mm;
        while ((mm = wantRe.exec(s)) !== null) want = Math.max(want, parseInt(mm[1], 10));
        const id = args.item_id;
        if (!id || seen.has(id)) continue;
        seen.add(id);
        let img = '';
        const im = s.match(/"url"\s*:\s*"(https?:\/\/[^"]+\.(?:jpg|jpeg|png|webp))[^"]*"/g);
        if (im) for (const u of im) if (u.includes('alicdn')) { img = u.slice(7, -1).replace(/\\\//g, '/'); break; }
        lines.push(JSON.stringify({
          item_id: id, title: (dp.title || '').replace(/\n/g, ' '),
          price: args.price || dp.soldPrice, want, seller: dp.userNick || '',
          area: it.exContent?.area || '', publishTime: args.publishTime, image: img
        }));
      }
      if (lines.length) fs.appendFileSync(OUT_FILE, lines.join('\n') + '\n');
      console.log(`[resp] items=${rl.length} new=${lines.length} total=${seen.size}`);
    } catch (e) { /* 忽略解析失败 */ }
  });

  await page.goto(`https://www.goofish.com/search?q=${encodeURIComponent(KEYWORD)}`, {
    waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(8000);

  for (let i = 0; i < PAGES; i++) {
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(800);
    const r = await page.evaluate(() => {
      const box = [...document.querySelectorAll('[class*="pagination"]')].find(e => e.offsetParent !== null);
      if (!box) return 'no-box';
      const btns = [...box.querySelectorAll('button[class*="arrow-container"]')];
      const right = btns.find(b => b.querySelector('[class*="arrow-right"]'));
      if (right && !right.disabled) { right.click(); return 'arrow'; }
      return 'disabled';
    });
    console.log(`[nav ${i + 1}] ${r}`);
    await page.waitForTimeout(3500);
  }
  console.log(`[done] total=${seen.size}`);
  await browser.close();
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
