/**
 * 闲鱼滑块验证自动求解器（反检测增强版）
 *
 * 使用 Playwright 启动有头浏览器，访问滑块验证页面或闲鱼主页，
 * 检测是否出现滑块验证弹窗，自动拖动滑块完成验证。
 *
 * 增强要点（对标商业版 sliderSolver.ts）：
 * 1. 注入用户 Cookie 保持登录态
 * 2. 智能检测 #nc_1 / .nc_wrapper / iframe#baxia-dialog 等阿里 Baxia 风控组件
 * 3. 反检测脚本：覆盖 webdriver/chrome/plugins/WebGL/Canvas 等指纹
 * 4. 模拟人工拖动：先快后慢、带随机抖动、模拟人类轨迹（page.mouse 真实事件）
 * 5. 多场景处理：加载转圈、点击框体重试、刷新弹窗、下载消息失败、登录页跳转
 * 6. 真人行动模拟：连续失败后关闭弹窗→刷新页面→冷静期→重新尝试
 * 7. 优先使用真实 Chrome 持久化上下文（降低自动化窗口被标记概率）
 * 8. 检测验证结果：成功（弹窗消失 / 出现成功标识）/ 失败（出现错误提示）
 *
 * 注：开源版不含 policy.js / 账号代理 / Python 脚本调用路径，已移除相关依赖。
 * 强化（与商业版对齐）：三种轨迹方案轮换（humanLikeDrag / humanLikeDragOutOfContainer / humanPhysicsDrag）、
 *   punish 状态检测、假成功防护、风险 cookies 清除、5 次重试、1 次真人行动模拟（2026-08-04 对齐商业版）。
 *   默认轨迹方案基于最小急动度剖面（Hogan 1984），与商业版 sliderSolve.py 的 human_physics_drag 一致。
 */
import { chromium, type Browser, type Page, type BrowserContextOptions, type Cookie } from 'playwright';
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';

export interface SlideSolveOptions {
  cookieStr?: string;
  targetUrl?: string;     // 默认为闲鱼消息页
  headless?: boolean;     // 默认 false（滑块识别需有头模式更稳定）
  maxRetries?: number;    // 单次会话最多重试次数，默认 5
  timeoutMs?: number;     // 单次操作超时，默认 30000
  signal?: AbortSignal;   // 调用方取消/服务关闭时及时释放浏览器
  saveFailureScreenshot?: boolean; // 默认关闭，避免在生产环境持久化敏感页面
}

export interface SlideSolveResult {
  ok: boolean;
  solved: boolean;            // 是否成功通过滑块
  captchaDetected: boolean;   // 是否检测到滑块
  attempts: number;           // 实际尝试次数
  error?: string;
  screenshotPath?: string;    // 失败时的截图路径
  durationMs: number;
  cookieStr?: string;         // 浏览器中的最终 Cookie（含 cna/isg/x5sec 等风控字段）
}

// 闲鱼消息页面（滑块验证通常在消息页弹出）
const DEFAULT_TARGET_URL = 'https://www.goofish.com/im';
const BAXIA_SELECTORS = [
  '#nc_1',              // 阿里 Baxia 标准滑块容器
  '.nc_wrapper',
  '#baxia-dialog',
  'iframe[src*="baxia"]',
  '.J_MIDDLEWARE_FRAME',
  'iframe[id*="baxia"]',
  '.slide-verify',
  '#nc_1_n1z',          // 滑块按钮
  '.btn_slide',
];

// ============================================================
// 风险 Cookies 清单（同步自商业版 sliderSolver.ts）
// ============================================================
// Baxia 在访问过程中通过 Set-Cookie 重新设置的 risk cookies，
// 刷新重试前必须清除，否则会形成"刷新→带 risk cookies→再次 punish→刷新"死循环。
const RISK_COOKIE_NAMES = [
  'x5secdata', 'x5sec', 'x5sectag', 'x5pref',
  'bx-cookie-test', 'tfstk', 'cbc', 'sca', 'isg',
];

// ============================================================
// Punish 状态检测（同步自商业版 sliderSolver.ts）
// ============================================================
// 关键纠正：mtop.taobao.idlemessage.pc.login.token 是 WS token 刷新 API 的名字，
// 不是 Cookie 失效信号！Baxia 挑战该 API 时 iframe URL 会含 login.token，但 Cookie 仍可能有效。
// 所有 punish URL（含 "_____tmd_____" 或 "punish"）统一视为 account_punished（可拖动）。
async function checkPunishedFrame(frame: any): Promise<boolean> {
  try {
    const url = frame?.url ? frame.url() : '';
    if (!url) return false;
    if (url.includes('_____tmd_____') || url.includes('punish')) {
      return true;
    }
  } catch { /* ignore */ }
  return false;
}

// 假成功检测：页面加载失败（chrome-error:// 或 chromewebdata）时不应判定为通过
function pageShowsLoadFailure(page: Page): boolean {
  try {
    const url = page.url();
    if (url.includes('chrome-error://') || url.includes('chromewebdata')) {
      return true;
    }
  } catch { /* ignore */ }
  return false;
}

// ============================================================
// 内联工具函数（替代商业版 policy.js 的依赖）
// ============================================================

function isProductionLike(env: string | undefined): boolean {
  if (!env) return false;
  const lower = env.toLowerCase();
  return lower === 'production' || lower === 'prod';
}

function safeErrorType(e: unknown): string {
  if (e instanceof Error) return e.name;
  if (typeof e === 'string') return e;
  return 'UnknownError';
}

function resolveHeadlessMode(headless?: boolean): boolean {
  // 显式传入的 headless 参数优先级最高。
  // 前台手动求解场景（前端传 headless=false）需要弹出 Chrome 窗口便于人工观察，
  // 即使本地用 .env (APP_ENV=production) 启动 crawler 也要尊重此选择。
  // 注意：Docker 容器内无 DISPLAY 时启动有头浏览器会失败，由调用方捕获错误。
  if (typeof headless === 'boolean') {
    return headless;
  }
  // 未显式传入时按环境兜底：生产环境默认无头（自动触发场景）
  if (isProductionLike(process.env.NODE_ENV || process.env.APP_ENV)) return true;
  if (process.env.HEADLESS === 'true') {
    return true;
  }
  if (process.env.HEADLESS === 'false') {
    return false;
  }
  return process.platform !== 'win32' && !process.env.DISPLAY;
}

// ============================================================
// 反检测脚本：在所有页面加载前注入，覆盖 Baxia 常用检测点
// ============================================================
// 5% 成功率的根因：navigator.webdriver 裸奔、plugins 伪造为数字数组、
// WebGL vendor 返回 SwiftShader、Canvas 指纹固定，Baxia 可直接识别为自动化。
// 本脚本覆盖：webdriver / platform / vendor / appVersion / userAgentData /
// chrome / plugins / languages / permissions / WebGL vendor&renderer /
// hardwareConcurrency / deviceMemory / Canvas 微扰动
const ANTI_DETECT_SCRIPT = `
(() => {
  try {
    // 1. 屏蔽 navigator.webdriver（与商业版对齐：getter 返回 false，并删除原型链属性）
    // 关键修复 2026-08-03：get: () => undefined 仍可被 'webdriver' in navigator 检测到
    // （属性存在但值为 undefined）；正常 Chrome（非自动化）navigator.webdriver 返回 false。
    try { delete Object.getPrototypeOf(navigator).webdriver; } catch (e) {}
    try {
      Object.defineProperty(Navigator.prototype, 'webdriver', {
        get: () => false,
        configurable: true,
      });
    } catch (e) {}
    try {
      Object.defineProperty(navigator, 'webdriver', {
        get: () => false,
        configurable: true,
      });
    } catch (e) {}

    // 1.1 伪装 navigator.platform（Navigator.prototype + 实例双层，与商业版对齐）
    // 关键修复 2026-08-03：Linux 服务器上 navigator.platform='Linux x86_64'，
    //   但 UA 伪装为 Windows，FireyeJS 会检测到 platform 与 UA 不一致直接拒绝加载。
    //   navigator.platform 是 Navigator.prototype 上的 getter，仅在实例上
    //   defineProperty 可能被原型链覆盖，必须双层伪装为 'Win32'。
    try {
      Object.defineProperty(Navigator.prototype, 'platform', {
        get: () => 'Win32',
        configurable: true,
      });
    } catch (e) {}
    try {
      Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32',
        configurable: true,
      });
    } catch (e) {}
    // 1.2 伪装 navigator.vendor（Chrome 为 'Google Inc.'）
    try {
      Object.defineProperty(navigator, 'vendor', {
        get: () => 'Google Inc.',
        configurable: true,
      });
    } catch (e) {}
    // 1.3 伪装 navigator.appVersion 与 UA 一致
    try {
      const ua = navigator.userAgent;
      const appVer = ua.replace(/^Mozilla\\//, '');
      Object.defineProperty(navigator, 'appVersion', {
        get: () => appVer,
        configurable: true,
      });
    } catch (e) {}

    // 1.4 覆盖 navigator.userAgentData（Chrome 90+ Client Hints，与商业版对齐）
    // 关键修复 2026-08-03：FireyeJS 通过 navigator.userAgentData.platform 检测真实操作系统，
    // 即使 navigator.platform 被覆盖为 Win32，userAgentData.platform 仍返回 'Linux'，
    // 导致 UA 与 platform 不一致，um.json 服务器拒绝设备指纹（返回 {"id":""}）。
    // 修复：覆盖 userAgentData 为完整的 Windows 指纹。
    try {
      const chromeVer = (navigator.userAgent.match(/Chrome\\/([\\d]+)/) || [, '146'])[1];
      const majorVer = chromeVer.split('.')[0];
      const fakeUAD = {
        brands: [
          { brand: 'Google Chrome', version: majorVer },
          { brand: 'Chromium', version: majorVer },
          { brand: 'Not.A/Brand', version: '8' },
        ],
        mobile: false,
        platform: 'Windows',
        getHighEntropyValues: function(hints) {
          return Promise.resolve({
            architecture: 'x86',
            bitness: '64',
            brands: fakeUAD.brands,
            fullVersionList: fakeUAD.brands,
            mobile: false,
            model: '',
            platform: 'Windows',
            platformVersion: '15.0.0',
            uaFullVersion: chromeVer,
            wow64: false,
          });
        },
        toJSON: function() {
          return { brands: fakeUAD.brands, mobile: false, platform: 'Windows' };
        },
      };
      Object.defineProperty(navigator, 'userAgentData', {
        get: () => fakeUAD,
        configurable: true,
      });
    } catch (e) {}

    // 2. 伪造 window.chrome 完整对象（真实 Chrome 有 runtime/app/csi/loadTimes）
    if (!window.chrome) {
      window.chrome = {};
    }
    if (!window.chrome.runtime) {
      window.chrome.runtime = {
        OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
        PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
      };
    }
    if (!window.chrome.csi) {
      window.chrome.csi = () => ({ startE: Date.now(), onloadT: Date.now(), pageT: 0, tran: 15 });
    }
    if (!window.chrome.loadTimes) {
      window.chrome.loadTimes = () => ({
        commitLoadTime: Date.now() / 1000 - 5,
        connectionInfo: 'h2',
        finishDocumentLoadTime: Date.now() / 1000 - 3,
        finishLoadTime: Date.now() / 1000 - 2,
        firstPaintAfterLoadTime: 0,
        firstPaintTime: Date.now() / 1000 - 4,
        navigationType: 'Other',
        npnNegotiatedProtocol: 'h2',
        requestTime: Date.now() / 1000 - 6,
        startLoadTime: Date.now() / 1000 - 6,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: true,
      });
    }

    // 3. 伪造 navigator.plugins 为真实 PluginArray 结构
    // 原实现返回 [1,2,3,4,5] 数字数组，instanceof 检测会立即识破
    const fakePlugin = (name, filename, description) => {
      const p = { name, filename, description, length: 1 };
      p[0] = { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' };
      p.item = (i) => p[i];
      p.namedItem = (n) => (p[0] && p[0].name === n ? p[0] : null);
      return p;
    };
    const plugins = [
      fakePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
    ];
    Object.defineProperty(navigator, 'plugins', {
      get: () => {
        const arr = plugins;
        arr.length = plugins.length;
        arr.item = (i) => plugins[i] || null;
        arr.namedItem = (n) => plugins.find(p => p.name === n) || null;
        arr.refresh = () => {};
        return arr;
      },
      configurable: true,
    });

    // 4. 伪造 navigator.languages
    Object.defineProperty(navigator, 'languages', {
      get: () => ['zh-CN', 'zh', 'en-US', 'en'],
      configurable: true,
    });

    // 5. 修复 navigator.permissions.query 与 Notification.permission 一致性
    if (window.Notification) {
      const origQuery = window.navigator.permissions && window.navigator.permissions.query;
      if (origQuery) {
        window.navigator.permissions.query = (params) => (
          params && params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission, onchange: null })
            : origQuery.call(window.navigator.permissions, params)
        );
      }
    }

    // 6. 伪造 WebGL vendor/renderer（headless/虚拟机返回 SwiftShader 是强机器人信号）
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
      // UNMASKED_VENDOR_WEBGL = 0x9245, UNMASKED_RENDERER_WEBGL = 0x9246
      if (param === 0x9245) return 'Google Inc. (NVIDIA)';
      if (param === 0x9246) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)';
      return getParameter.call(this, param);
    };
    // WebGL2 同样处理
    if (typeof WebGL2RenderingContext !== 'undefined') {
      const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function(param) {
        if (param === 0x9245) return 'Google Inc. (NVIDIA)';
        if (param === 0x9246) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)';
        return getParameter2.call(this, param);
      };
    }

    // 7. 伪造 navigator.hardwareConcurrency 和 deviceMemory
    // 真人常见值：4-8 核，4-8GB 内存
    Object.defineProperty(navigator, 'hardwareConcurrency', {
      get: () => 8,
      configurable: true,
    });
    Object.defineProperty(navigator, 'deviceMemory', {
      get: () => 8,
      configurable: true,
    });

    // 8. Canvas 指纹微扰动（与商业版对齐）：在 toDataURL 返回值中注入微小噪声
    // 真人 Canvas 指纹因显卡/驱动差异天然不同，自动化环境 Canvas 指纹固定且可被 fingerprintjs 库识别
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(...args) {
      const ctx = this.getContext('2d');
      if (ctx) {
        try {
          const w = this.width, h = this.height;
          if (w > 0 && h > 0) {
            const img = ctx.getImageData(0, 0, w, h);
            // 在 R 通道注入 ±1 的微小噪声（视觉不可见，但改变指纹哈希）
            for (let i = 0; i < img.data.length; i += 4) {
              if (Math.random() < 0.03) {  // 3% 像素扰动
                img.data[i] = (img.data[i] + (Math.random() < 0.5 ? -1 : 1)) & 0xff;
              }
            }
            ctx.putImageData(img, 0, 0);
          }
        } catch (e) {}
      }
      return origToDataURL.apply(this, args);
    };

    // 9. 隐藏 Playwright/CDP 注入痕迹
    // 移除 window.cdc_ 开头的属性（Chrome DevTools Controller 注入的标记）
    for (const key of Object.keys(window)) {
      if (key.startsWith('cdc_')) {
        try { delete window[key]; } catch (e) {}
      }
    }
  } catch (e) {}
})();
`;

/**
 * 将 Cookie 字符串解析为 Playwright Cookie 数组
 */
export function parseCookieString(cookieStr: string, domain: string = '.goofish.com'): Cookie[] {
  if (!cookieStr) return [];
  const cookies: Cookie[] = [];
  const expires = Math.floor(Date.now() / 1000) + 30 * 24 * 3600;
  for (const part of cookieStr.split(';')) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx <= 0) continue;
    const name = trimmed.substring(0, eqIdx).trim();
    const value = trimmed.substring(eqIdx + 1).trim();
    if (!name) continue;
    cookies.push({
      name,
      value,
      domain,
      path: '/',
      expires,
      httpOnly: false,
      secure: true,
      sameSite: 'Lax',
    } as Cookie);
  }
  return cookies;
}

/**
 * 导出浏览器上下文中的 Cookie 为字符串（仅 goofish 域）
 */
async function exportContextCookies(context: any): Promise<string | undefined> {
  try {
    const cookies: Cookie[] = await context.cookies();
    if (!cookies?.length) return undefined;
    // 仅导出 goofish 相关域，避免把 Cookie 复制到无关域
    const goofishCookies = cookies.filter((c) => {
      const d = (c.domain || '').replace(/^\./, '');
      return d === 'goofish.com' || d.endsWith('.goofish.com') || d === 'www.goofish.com';
    });
    const list = goofishCookies.length ? goofishCookies : cookies;
    return list
      .filter((c) => c.name)
      .map((c) => `${c.name}=${c.value ?? ''}`)
      .join('; ');
  } catch {
    return undefined;
  }
}

async function saveDebugScreenshot(page: Page, label: string): Promise<string | undefined> {
  try {
    const debugDir = path.join(process.cwd(), 'screenshots');
    await fs.mkdir(debugDir, { recursive: true });
    const file = path.join(debugDir, `${label}-${Date.now()}.png`);
    await page.screenshot({ path: file, fullPage: false });
    console.log(`[SliderSolver] 截图: ${file}`);
    return file;
  } catch {
    return undefined;
  }
}

/**
 * 检测页面是否出现滑块验证组件
 */
async function detectCaptcha(page: Page): Promise<{ detected: boolean; selector?: string; iframe?: any }> {
  // 直接在主文档检测
  for (const selector of BAXIA_SELECTORS) {
    try {
      const elem = await page.$(selector);
      if (elem && await elem.isVisible()) {
        return { detected: true, selector };
      }
    } catch {
      // ignore
    }
  }

  // 检测 iframe 中的滑块
  const frames = page.frames();
  for (const frame of frames) {
    if (frame === page.mainFrame()) continue;
    for (const selector of BAXIA_SELECTORS) {
      try {
        const elem = await frame.$(selector);
        if (elem && await elem.isVisible()) {
          return { detected: true, selector, iframe: frame };
        }
      } catch {
        // ignore
      }
    }
  }

  return { detected: false };
}

/**
 * 在指定 frame 及其所有子 frame 中递归查找滑块按钮
 * baxia 滑块通常嵌套在多层 iframe 中，需要递归查找
 */
async function findSliderButtonInFrames(frame: any, selectors: string[]): Promise<{ button: any; frame: any } | null> {
  // 当前 frame 查找
  for (const sel of selectors) {
    try {
      const button = await frame.$(sel);
      if (button) {
        const box = await button.boundingBox();
        if (box && box.width > 0) {
          return { button, frame };
        }
      }
    } catch {
      // ignore
    }
  }
  // 递归查找子 frame
  let childFrames: any[] = [];
  try {
    childFrames = frame.childFrames ? frame.childFrames() : [];
  } catch {
    childFrames = [];
  }
  for (const childFrame of childFrames) {
    try {
      const result = await findSliderButtonInFrames(childFrame, selectors);
      if (result) return result;
    } catch {
      // ignore
    }
  }
  return null;
}

/**
 * 兜底：在 frame 内通过启发式查找滑块按钮。
 * 当所有已知 selector 都匹配不到时调用，在常见的 Baxia 容器内查找
 * "宽度 24-60px、高度 24-60px、绝对/相对定位、cursor:pointer 或可拖动"的小方块元素。
 *
 * 这是必要的兜底，因为阿里 Baxia 滑块组件会定期更换 DOM 结构和 class 名，
 * 静态 selector 列表无法覆盖所有版本。
 */
async function findSliderButtonByHeuristic(frame: any): Promise<{ button: any; frame: any } | null> {
  // 启发式 evaluate 脚本：在常见容器内查找疑似滑块按钮
  const heuristicScript = `
    () => {
      // 候选容器：Baxia 标准容器 + 任何看起来像滑块的容器
      const containerSelectors = [
        '#nc_1', '.nc_wrapper', '.J_MIDDLEWARE_FRAME',
        '.slide-verify', '#baxia-dialog', '.nc-container',
        '[class*="nc_"]', '[id*="nc_"]', '[class*="slider"]', '[class*="slide"]'
      ];
      const containers = new Set();
      for (const sel of containerSelectors) {
        document.querySelectorAll(sel).forEach(el => containers.add(el));
      }
      // 兜底：整个文档
      containers.add(document);

      // 在容器内查找疑似滑块按钮：小方块 + 可拖动样式
      const candidates = [];
      for (const container of containers) {
        const elems = container.querySelectorAll
          ? container.querySelectorAll('div, span, a, button, i, em')
          : [];
        for (const el of elems) {
          const rect = el.getBoundingClientRect();
          // 滑块按钮尺寸：宽 24-60px，高 24-60px
          if (rect.width < 24 || rect.width > 60) continue;
          if (rect.height < 24 || rect.height > 60) continue;
          // 必须可见
          if (rect.bottom <= 0 || rect.right <= 0) continue;
          if (window.getComputedStyle(el).visibility === 'hidden') continue;
          if (window.getComputedStyle(el).display === 'none') continue;
          // cursor 指针或可拖动样式
          const cursor = window.getComputedStyle(el).cursor;
          const isDraggable = cursor === 'pointer' || cursor === 'move' || cursor === 'grab'
            || el.draggable || el.getAttribute('draggable') === 'true';
          // 排除明显是文字、图标、链接的元素
          const text = (el.innerText || '').trim();
          if (text.length > 5) continue;
          // 评分：可拖动 +5，正方形 +3，绝对/相对定位 +2
          let score = 0;
          if (isDraggable) score += 5;
          const sizeDiff = Math.abs(rect.width - rect.height);
          if (sizeDiff < 8) score += 3;
          const position = window.getComputedStyle(el).position;
          if (position === 'absolute' || position === 'relative') score += 2;
          candidates.push({ selector: el, score, x: rect.left, y: rect.top, w: rect.width, h: rect.height });
        }
      }
      // 选评分最高的
      candidates.sort((a, b) => b.score - a.score);
      return candidates.slice(0, 3).map(c => ({
        x: c.x, y: c.y, w: c.w, h: c.h, score: c.score
      }));
    }
  `;
  try {
    const results = await frame.evaluate(heuristicScript);
    if (Array.isArray(results) && results.length > 0) {
      const top = results[0];
      if (top.score >= 5) {
        console.log(`[SliderSolver] 启发式找到疑似滑块按钮: x=${top.x}, y=${top.y}, w=${top.w}, h=${top.h}, score=${top.score}`);
        // 用 elementHandle 的方式重新获取：通过 boundingBox 找到对应元素
        // 由于 evaluate 不能直接返回 ElementHandle，我们用 frame.$$ 配合筛选
        // 简化方案：找到所有候选元素，逐个对比 boundingBox
        const allCandidates = await frame.$$('div, span, a, button, i, em');
        for (const el of allCandidates) {
          try {
            const box = await el.boundingBox();
            if (!box) continue;
            if (Math.abs(box.x - top.x) < 2 && Math.abs(box.y - top.y) < 2
                && Math.abs(box.width - top.w) < 2 && Math.abs(box.height - top.h) < 2) {
              return { button: el, frame };
            }
          } catch {
            // ignore
          }
        }
      }
    }
  } catch (e: any) {
    console.log(`[SliderSolver] 启发式查找失败: ${safeErrorType(e)}`);
  }
  return null;
}

/**
 * 获取滑块按钮位置和滑块轨道宽度
 * 递归查找所有嵌套 iframe 中的滑块按钮（baxia 滑块通常在多层 iframe 中）
 *
 * 查找顺序：
 * 1. 已知 selector 列表（覆盖大多数 Baxia 版本）
 * 2. 启发式查找（兜底，应对阿里定期更换 DOM 结构）
 */
async function getSliderInfo(frame: any): Promise<{ button: any; trackWidth: number; buttonBox: { x: number; y: number; width: number; height: number }; ownerFrame: any } | null> {
  // 扩充 selector 列表，覆盖 Baxia 各个版本（含 2024+ 新版）
  const buttonSelectors = [
    '#nc_1_n1z',          // Baxia 经典滑块按钮
    '.btn_slide',         // 通用 .btn_slide
    '.nc_iconfont',       // Baxia 图标按钮
    '.slide-btn',         // 通用 slide-btn
    '#nc_1_n1t',          // Baxia 备用
    '.nc-lang-cnt',       // Baxia 语言按钮
    '[data-role="slider"]',  // data-role 标记
    '#nc_1_n1z .icon',    // 嵌套图标
    '.btn_slide > i',     // 嵌套图标
    '#aliyunCaptcha-sliding-slider',  // 阿里云验证码 2.0
    '#nc_1_n1z[style]',   // 带内联样式的滑块按钮
    '.J_MIDDLEWARE_FRAME .btn_slide',  // 中间件框架内
    'span.nc_iconfont',   // span 包裹的图标
    '#baxia-dialog .btn_slide',  // baxia-dialog 内
    'div[role="button"][class*="slide"]',  // role=button 的滑块
    'div[draggable="true"][class*="slide"]',  // 可拖动滑块
  ];
  // 递归查找所有 frame（包括嵌套 iframe）中的滑块按钮
  let found = await findSliderButtonInFrames(frame, buttonSelectors);

  // 兜底：selector 都匹配不到时，用启发式查找
  if (!found) {
    let foundHeuristic = false;
    // 在主 frame 和所有子 frame 中尝试启发式查找
    const framesToTry: any[] = [frame];
    try {
      const childFrames = frame.childFrames ? frame.childFrames() : [];
      framesToTry.push(...childFrames);
    } catch { /* ignore */ }
    for (const tryFrame of framesToTry) {
      try {
        const heuristicResult = await findSliderButtonByHeuristic(tryFrame);
        if (heuristicResult) {
          found = heuristicResult;
          foundHeuristic = true;
          console.log(`[SliderSolver] 启发式查找成功，frame URL: ${tryFrame.url()}`);
          break;
        }
      } catch {
        // ignore
      }
    }
    if (!foundHeuristic) {
      // 打印详细调试信息，帮助诊断
      const frameUrls: string[] = [];
      try {
        frameUrls.push(frame.url());
        const allFrames = frame.childFrames ? frame.childFrames() : [];
        for (const f of allFrames) {
          try { frameUrls.push(f.url()); } catch { /* ignore */ }
        }
      } catch { /* ignore */ }
      console.warn(`[SliderSolver] getSliderInfo 找不到滑块按钮。已尝试 selector: ${buttonSelectors.join(', ')}`);
      console.warn(`[SliderSolver] 涉及 frame URLs: ${frameUrls.slice(0, 5).join(' | ')}`);
    }
  }

  if (!found) return null;

  const { button, frame: ownerFrame } = found;
  const box = await button.boundingBox();
  if (!box) {
    console.warn('[SliderSolver] 滑块按钮 boundingBox 返回 null（可能元素已从 DOM 中移除）');
    return null;
  }

  // 在按钮所在的 frame 中查找滑块轨道
  const trackSelectors = ['.nc_scale', '.scale_text', '.slide-track', '#nc_1__scale', '.nc-lang',
    '.nc_wrapper', '.slide-verify-track', '.slider-track', '[class*="track"]'];
  let trackWidth = 300; // 默认轨道宽度
  for (const tsel of trackSelectors) {
    try {
      const track = await ownerFrame.$(tsel);
      if (track) {
        const trackBox = await track.boundingBox();
        if (trackBox && trackBox.width > 0) {
          // 可拖动距离 = 轨道宽 - 按钮宽；钳制到常见区间，避免过冲不足/过度
          trackWidth = Math.max(180, Math.min(360, trackBox.width - box.width));
          break;
        }
      }
    } catch {
      // ignore
    }
  }
  console.log(`[SliderSolver] 滑块信息: buttonBox={x:${box.x.toFixed(0)}, y:${box.y.toFixed(0)}, w:${box.width.toFixed(0)}, h:${box.height.toFixed(0)}}, trackWidth=${trackWidth.toFixed(0)}`);
  return { button, trackWidth, buttonBox: box, ownerFrame };
}

/**
 * CDP 鼠标封装：手动设置 deltaX/deltaY，修复 movementX/movementY 恒为 0 的问题（C2.4）
 *
 * 关键修复 2026-08-01：Playwright 的 page.mouse.move() 通过 CDP Input.dispatchMouseEvent
 * 发送事件时不携带 deltaX/deltaY 参数，导致 event.movementX/movementY 始终为 0。
 * 真实鼠标拖动时 movementX/movementY 反映移动增量，Baxia FireyeJS 检测到
 * movementX=0 的拖动事件会直接判定为机器人（真实鼠标拖动时 movementX 不可能全为 0）。
 * 修复方案：用 CDP 直接发送 Input.dispatchMouseEvent，手动设置 deltaX/deltaY。
 */
class CdpMouse {
  private readonly client: any;
  private x: number;
  private y: number;
  private pressed = false;

  constructor(client: any, startX: number, startY: number) {
    this.client = client;
    this.x = startX;
    this.y = startY;
  }

  private async dispatch(type: string, x: number, y: number, buttons: number, extra: Record<string, unknown> = {}): Promise<void> {
    const dx = Math.round((x - this.x) * 100) / 100;
    const dy = Math.round((y - this.y) * 100) / 100;
    this.x = x;
    this.y = y;
    await this.client.send('Input.dispatchMouseEvent', {
      type,
      x,
      y,
      deltaX: dx,
      deltaY: dy,
      button: 'left',
      buttons,
      modifiers: 0,
      timestamp: 0,
      ...extra,
    });
  }

  /** 移动（steps>1 时线性插值，逐点附带正确 deltaX/deltaY） */
  async move(x: number, y: number, steps = 1): Promise<void> {
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      await this.dispatch('mouseMoved', this.x + (x - this.x) * t, this.y + (y - this.y) * t, this.pressed ? 1 : 0);
    }
  }

  async down(x: number, y: number): Promise<void> {
    await this.dispatch('mousePressed', x, y, 1, { clickCount: 1 });
    this.pressed = true;
  }

  async up(x: number, y: number): Promise<void> {
    await this.dispatch('mouseReleased', x, y, 0, { clickCount: 1 });
    this.pressed = false;
  }
}

/** 创建拖动鼠标控制：优先 CDP（带 deltaX/deltaY），CDP 不可用时回退 page.mouse */
async function createDragMouse(page: Page, startX: number, startY: number): Promise<{
  move(x: number, y: number, steps?: number): Promise<void>;
  down(x: number, y: number): Promise<void>;
  up(x: number, y: number): Promise<void>;
}> {
  try {
    const client = await page.context().newCDPSession(page);
    const cdp = new CdpMouse(client, startX, startY);
    return {
      move: (x, y, steps = 1) => cdp.move(x, y, steps),
      down: (x, y) => cdp.down(x, y),
      up: (x, y) => cdp.up(x, y),
    };
  } catch {
    // CDP 会话创建失败（极端情况），回退 page.mouse（无 delta，但保证流程可用）
    return {
      move: (x, y, steps = 1) => page.mouse.move(x, y, { steps }),
      down: () => page.mouse.down(),
      up: () => page.mouse.up(),
    };
  }
}

/**
 * 模拟人工拖动滑块（增强版：对抗 Baxia 风控检测）
 *
 * 风控判定机器人/程序的常见维度：
 * 1. 事件真实性：合成事件 isTrusted=false → 机器人。本实现改用 page.mouse API 生成真实事件。
 * 2. 轨迹直线：Y坐标不变、X单调递增 → 机器人。本实现加入 Y方向抖动和偶尔回退。
 * 3. 匀速滑动：没有加速/减速过程 → 机器人。本实现使用三阶段速度曲线。
 * 4. 按下即移动：无"按下-思考-移动"过程 → 机器人。本实现按下后停顿。
 * 5. 总时长过短：<500ms → 机器人。本实现总时长 > 1.5秒。
 * 6. 无过冲/回退：精确停在终点 → 机器人。本实现终点过冲后回退。
 * 7. 步间间隔均匀：完全相同的时间间隔 → 机器人。本实现每步间隔随机化 + 钟形权重。
 * 8. 释放前无停顿：按下即释放 → 机器人。本实现释放前停顿。
 */
async function humanLikeDrag(
  page: Page,
  frame: any,
  button: any,
  startX: number,
  startY: number,
  distance: number,
  attempt: number = 1
): Promise<void> {
  // 入口诊断日志：确认 humanLikeDrag 被实际调用，便于排查"没看到滑动"问题
  // 坐标合理性检查：startX/startY 应在 viewport（1280x800）内、distance 应为正数
  const coordValid = Number.isFinite(startX) && Number.isFinite(startY)
    && Number.isFinite(distance) && distance > 0
    && startX > 0 && startY > 0 && startX < 2000 && startY < 2000;
  console.log(`[SliderSolver] humanLikeDrag 入口: startX=${startX.toFixed(1)}, startY=${startY.toFixed(1)}, distance=${distance.toFixed(1)}, attempt=${attempt}, coordValid=${coordValid}`);
  if (!coordValid) {
    console.warn(`[SliderSolver] 坐标异常，可能导致鼠标在错误位置拖动。可能原因：boundingBox 返回错误坐标（cross-origin iframe）`);
  }

  // 根据 attempt 选择不同的速度策略（每次重试速度不同）
  // 2026-08-04 对齐商业版 5 档参数：总时长落在 2-3.5s 区间，
  // 原实现 30-40 步/20-50ms ≈ 0.8-1.8s 明显偏快（Baxia 通过拖动速度识别机器人）
  let stepsBase: number;
  let stepDelayMin: number;
  let stepDelayMax: number;
  // 中间停顿点位置（progress 0.3-0.7 之间随机）
  const pausePoint = 0.3 + Math.random() * 0.4;
  const pauseDurationMs = 300;  // 停顿0.3秒
  let pausePoints: number[] = [];

  switch (attempt) {
    case 1:
      // 标准速度（真人模拟）：50-65步，30-70ms间隔 + 1个中间停顿，总时长约 2-3.5秒
      stepsBase = 50;
      stepDelayMin = 30;
      stepDelayMax = 70;
      pausePoints = [pausePoint];
      break;
    case 2:
      // 中慢速：55-70步，40-80ms间隔 + 1个中间停顿，总时长约 2.5-4.5秒
      stepsBase = 55;
      stepDelayMin = 40;
      stepDelayMax = 80;
      pausePoints = [pausePoint];
      break;
    case 3:
      // 标准速度：45-60步，25-60ms间隔，无停顿，总时长约 1.5-3秒
      stepsBase = 45;
      stepDelayMin = 25;
      stepDelayMax = 60;
      break;
    case 4:
      // 慢速：60-75步，50-100ms间隔 + 2个中间停顿，总时长约 4-6秒
      stepsBase = 60;
      stepDelayMin = 50;
      stepDelayMax = 100;
      pausePoints = [pausePoint, 0.6 + Math.random() * 0.2];
      break;
    default:
      // attempt >= 5: 随机策略组合（不设停顿，避免太慢）
      stepsBase = 40 + Math.floor(Math.random() * 20);
      stepDelayMin = 30 + Math.floor(Math.random() * 30);
      stepDelayMax = stepDelayMin + 30 + Math.floor(Math.random() * 40);
      pausePoints = [];
      break;
  }

  const steps = stepsBase + Math.floor(Math.random() * 15);  // 加少量随机性
  const totalEstMs = steps * ((stepDelayMin + stepDelayMax) / 2) + pausePoints.length * pauseDurationMs;
  console.log(`[SliderSolver] 拖动策略: attempt=${attempt}, steps=${steps}, delay=${stepDelayMin}-${stepDelayMax}ms, pauses=${pausePoints.length}, 预计总时长≈${totalEstMs}ms`);

  // 起点在按钮中心附近随机偏移，避免永远点死几何中心
  const actualStartX = startX + (Math.random() - 0.5) * 8;
  const actualStartY = startY + (Math.random() - 0.5) * 6;

  // 1. 接近轨迹：从按钮附近随机点移入（非瞬移到按钮中心）
  // 使用 steps:8 让鼠标移动有可视化轨迹（headful 模式下肉眼可见）
  const approachAngle = Math.random() * Math.PI * 2;
  const approachDist = 40 + Math.random() * 80;
  const approachX = actualStartX + Math.cos(approachAngle) * approachDist;
  const approachY = actualStartY + Math.sin(approachAngle) * approachDist;
  await page.mouse.move(approachX, approachY, { steps: 8 });
  await page.waitForTimeout(80 + Math.random() * 120);
  const approachSteps = 3 + Math.floor(Math.random() * 3);
  for (let i = 1; i <= approachSteps; i++) {
    const t = i / approachSteps;
    const eased = t * t * (3 - 2 * t);
    await page.mouse.move(
      approachX + (actualStartX - approachX) * eased,
      approachY + (actualStartY - approachY) * eased,
      { steps: 5 },
    );
    await page.waitForTimeout(12 + Math.random() * 25);
  }
  // 移动到按钮后的"思考"停顿（100-250ms）
  await page.waitForTimeout(100 + Math.random() * 150);

  // 2. 鼠标按下（真实 mousedown 事件）
  // 使用 CDP 鼠标控制（带 deltaX/deltaY），修复 movementX/movementY=0 的机器人特征
  const mouse = await createDragMouse(page, actualStartX, actualStartY);
  await mouse.down(actualStartX, actualStartY);
  // 按下后短暂停顿（80-180ms，模拟按下后开始滑动）
  await page.waitForTimeout(80 + Math.random() * 100);
  // 按下后微小漂移（真人按下到开始拖动之间鼠标常有 1-2px 漂移，非完美静止）
  await mouse.move(actualStartX + (Math.random() - 0.5) * 3, actualStartY + (Math.random() - 0.5) * 3, 3);
  await page.waitForTimeout(30 + Math.random() * 50);

  // 3. 分多步拖动，使用不对称三阶段速度曲线（真人加速段短、匀速段长、减速段居中）
  let pauseIdx = 0;
  let lastX = actualStartX;
  // Y 惯性：保留上一步 Y 值，本步在其基础上小幅偏移，模拟手部运动的连续性
  let lastY = actualStartY;
  // 弧形 Y 基线方向（随机向上或向下），幅度 3-8px
  const arcDirection = Math.random() < 0.5 ? -1 : 1;
  const arcAmplitude = 3 + Math.random() * 5;

  for (let i = 1; i <= steps; i++) {
    const progress = i / steps;
    // 不对称三阶段权重：起步(0-0.2)慢、匀速(0.2-0.7)快、减速(0.7-1.0)居中
    let speedWeight: number;
    if (progress < 0.2) {
      // 起步阶段：权重从 1 衰减到 0.3（慢→快）
      speedWeight = 1 - 0.7 * (progress / 0.2);
    } else if (progress < 0.7) {
      // 匀速阶段：权重 0.2-0.4（快速滑动，略有波动）
      speedWeight = 0.25 + 0.15 * Math.sin(progress * Math.PI * 4);
    } else {
      // 减速阶段：权重从 0.4 升到 1（快→慢），比加速段更长
      speedWeight = 0.4 + 0.6 * ((progress - 0.7) / 0.3);
    }
    // 位移使用 ease-in-out 变体：起步更慢（progress^2.5），更接近真人初始犹豫
    const eased = Math.pow(progress, 2.5) / (Math.pow(progress, 2.5) + Math.pow(1 - progress, 2.5));
    let targetX = actualStartX + distance * eased;

    // 偶尔轻微回退（5%概率，模拟真人手抖回退，仅在滑动中段）
    if (Math.random() < 0.05 && i > 3 && i < steps - 3) {
      targetX = lastX - (2 + Math.random() * 3);
    }

    // Y 方向：弧形基线 + 惯性延续 + 小幅抖动
    const arcOffset = arcDirection * arcAmplitude * Math.sin(Math.PI * progress);
    const yDrift = (Math.random() - 0.5) * 6;
    let currentY = lastY + yDrift;
    const targetY = actualStartY + arcOffset;
    currentY = currentY * 0.6 + targetY * 0.4;
    lastY = currentY;

    // 使用 CDP 鼠标移动生成真实 mousemove 事件（带 deltaX/deltaY）
    // steps: 3 让每步移动有 3 个 mousemove 事件，肉眼可见鼠标在"滑"而非瞬移
    // 同时保持每个 mousemove 事件之间的距离合理（约 3-5px），符合 Baxia 对轨迹密度的要求
    await mouse.move(targetX, currentY, 3);

    // 每步间隔：对数正态分布（多数快、偶尔慢，比均匀分布更接近真人）
    const medianDelay = stepDelayMin + (stepDelayMax - stepDelayMin) * 0.4;
    const sigma = 0.5;
    const u1 = Math.random() || 1e-10;
    const u2 = Math.random();
    const normalRand = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    const logNormalDelay = medianDelay * Math.exp(sigma * normalRand);
    // 限制在 [stepDelayMin, stepDelayMax*2] 范围，避免极端值
    const baseDelay = Math.max(stepDelayMin, Math.min(stepDelayMax * 2, logNormalDelay));
    // 叠加三阶段速度权重（慢阶段延迟大，快阶段延迟小）
    const delay = baseDelay * speedWeight;
    await page.waitForTimeout(delay);

    lastX = targetX;

    // 中间停顿0.3秒（模拟真人滑动时的犹豫）
    if (pauseIdx < pausePoints.length && progress >= pausePoints[pauseIdx]) {
      console.log(`[SliderSolver] 在 progress=${progress.toFixed(2)} 处停顿 ${pauseDurationMs}ms`);
      await page.waitForTimeout(pauseDurationMs);
      pauseIdx++;
    }
  }

  // 4. 终点过冲后回退（人类拖动常见行为：滑过头再退回来）
  await page.waitForTimeout(30 + Math.random() * 70);
  const overshoot = 5 + Math.random() * 8;  // 过冲 5-13px
  await mouse.move(actualStartX + distance + overshoot, actualStartY + (Math.random() - 0.5) * 10, 4);
  await page.waitForTimeout(50 + Math.random() * 80);
  await mouse.move(actualStartX + distance, actualStartY + (Math.random() - 0.5) * 6, 4);

  // 5. 释放前微调（真人释放前常有 1-2 次微小位置修正，非完美静止释放）
  const numAdjustments = Math.random() < 0.7 ? 1 : 2;  // 70% 概率 1 次，30% 概率 2 次
  for (let a = 0; a < numAdjustments; a++) {
    await page.waitForTimeout(40 + Math.random() * 60);
    const adjustX = (Math.random() - 0.5) * 4;
    const adjustY = (Math.random() - 0.5) * 4;
    await mouse.move(actualStartX + distance + adjustX, actualStartY + adjustY, 2);
  }
  // 释放前短暂停顿（50-120ms）
  await page.waitForTimeout(50 + Math.random() * 70);
  // 鼠标释放（真实 mouseup 事件）
  await mouse.up(actualStartX + distance, lastY);
}

/**
 * 超出容器范围的真人拖动（增加滑块求解成功率的方法之二）
 *
 * 现有 humanLikeDrag 的 Y 方向抖动仅 ±8 像素，鼠标始终在滑块容器内。
 * 但实际场景中，真人拖动滑块时鼠标可随意超出弹窗容器范围（向上/向下大幅偏移），
 * 只要鼠标已按下且整体向右移动，Baxia 仍会判定为有效滑动。
 *
 * 本函数与 humanLikeDrag 互补：
 * - humanLikeDrag: 容器内精确拖动（Y ±8px）
 * - 本函数: 容器外随意拖动（Y ±50-120px），模拟真人不受约束的手部移动
 */
async function humanLikeDragOutOfContainer(
  page: Page,
  frame: any,
  button: any,
  startX: number,
  startY: number,
  distance: number,
  attempt: number = 1
): Promise<void> {
  // 速度策略与 humanLikeDrag 一致，但步数略多（因为要走出容器拐点）
  let stepsBase: number;
  let stepDelayMin: number;
  let stepDelayMax: number;
  switch (attempt) {
    case 1:
      stepsBase = 35; stepDelayMin = 25; stepDelayMax = 55;
      break;
    case 2:
      stepsBase = 40; stepDelayMin = 30; stepDelayMax = 70;
      break;
    case 3:
      stepsBase = 30; stepDelayMin = 20; stepDelayMax = 50;
      break;
    default:
      stepsBase = 35 + Math.floor(Math.random() * 15);
      stepDelayMin = 25 + Math.floor(Math.random() * 30);
      stepDelayMax = stepDelayMin + 30 + Math.floor(Math.random() * 40);
      break;
  }
  const steps = stepsBase + Math.floor(Math.random() * 10);
  console.log(`[SliderSolver] 超出容器拖动策略: attempt=${attempt}, steps=${steps}, delay=${stepDelayMin}-${stepDelayMax}ms`);

  // 1. 移动到按钮位置（真人会先把鼠标移到按钮上）
  await page.mouse.move(startX, startY, { steps: 5 });
  await page.waitForTimeout(100 + Math.random() * 150);

  // 2. 鼠标按下（CDP 带 deltaX/deltaY，修复 movementX=0 机器人特征）
  const mouse = await createDragMouse(page, startX, startY);
  await mouse.down(startX, startY);
  await page.waitForTimeout(80 + Math.random() * 100);

  // 3. 生成 2-3 个"出容器拐点"
  // 每个拐点是一个 Y 方向大幅偏移的位置（±50-120px），模拟真人把鼠标拖出弹窗
  const numOutPoints = 2 + Math.floor(Math.random() * 2);  // 2-3 个
  const outPoints: Array<{ progress: number; yOffset: number }> = [];
  for (let i = 0; i < numOutPoints; i++) {
    // 拐点进度均匀分布在 0.2-0.8 之间
    const prog = 0.2 + (0.6 * (i + 1) / (numOutPoints + 1)) + (Math.random() - 0.5) * 0.1;
    // Y 偏移方向交替（一上一下），幅度 50-120px
    const direction = (i % 2 === 0) ? -1 : 1;
    const magnitude = 50 + Math.random() * 70;  // 50-120px
    outPoints.push({ progress: Math.max(0.15, Math.min(0.85, prog)), yOffset: direction * magnitude });
  }
  console.log(`[SliderSolver] 出容器拐点: ${outPoints.map(p => `p=${p.progress.toFixed(2)},y=${p.yOffset.toFixed(0)}px`).join(' | ')}`);

  // 4. 分多步拖动，X 单调向右递增，Y 在拐点处大幅偏出容器
  let lastX = startX;
  for (let i = 1; i <= steps; i++) {
    const progress = i / steps;
    const bellCurve = Math.sin(Math.PI * progress);
    const eased = progress * progress * (3 - 2 * progress);
    let targetX = startX + distance * eased;

    // 偶尔轻微回退（5%概率，模拟手抖回退，仅在滑动中段）
    if (Math.random() < 0.05 && i > 3 && i < steps - 3) {
      targetX = lastX - (2 + Math.random() * 3);
    }

    // Y 方向：基础弧形 + 拐点影响 + 随机抖动
    const baseArc = Math.sin(Math.PI * progress) * 5;  // 基础弧度 ±5px
    let yOffset = 0;
    for (const op of outPoints) {
      const dist = Math.abs(progress - op.progress);
      if (dist < 0.15) {
        // 高斯衰减：离拐点越近影响越大
        const influence = Math.exp(-(dist * dist) / (2 * 0.05 * 0.05));
        yOffset += op.yOffset * influence;
      }
    }
    // 小幅随机抖动（±5px）
    const jitter = (Math.random() - 0.5) * 10;
    const currentY = startY + baseArc + yOffset + jitter;

    await mouse.move(targetX, currentY, 1);

    // 每步间隔随机化 + 钟形权重
    const delayWeight = 1 - bellCurve * 0.5;
    const baseDelay = stepDelayMin + Math.random() * (stepDelayMax - stepDelayMin);
    const delay = baseDelay * delayWeight;
    await page.waitForTimeout(delay);

    lastX = targetX;
  }

  // 5. 终点过冲后回退（Y 也在容器外，模拟真人手部自然位置）
  await page.waitForTimeout(30 + Math.random() * 70);
  const overshoot = 5 + Math.random() * 10;
  // 过冲点 Y 偏移（可能在容器外）
  const overshootYOffset = (Math.random() - 0.5) * 40;  // ±20px
  await mouse.move(startX + distance + overshoot, startY + overshootYOffset, 2);
  await page.waitForTimeout(50 + Math.random() * 80);
  // 回到终点（Y 仍有偏移，可能在容器外）
  const endYOffset = (Math.random() - 0.5) * 30;  // ±15px
  await mouse.move(startX + distance, startY + endYOffset, 2);

  // 6. 释放前停顿
  await page.waitForTimeout(50 + Math.random() * 70);
  await mouse.up(startX + distance, startY + endYOffset);
}

/**
 * 基于物理模型的人类拖动模拟（最小急动度剖面）
 *
 * 同步自商业版 sliderSolve.py 的 human_physics_drag 默认轨迹方案。
 * 基于 Hogan 1984 / Flash & Hogan 1985 的最小急动度剖面（Minimum Jerk Profile），
 * 这是人类 reaching 运动的最优数学模型，产生平滑的钟形速度曲线。
 *
 * 反检测特性：
 * 1. 起始位置噪声 ±0.5px（人类无法像素级精准定位）
 * 2. 距离噪声 ±1px（Baxia 精度要求 ±1-2px）
 * 3. 总时长 700-1300ms（人类拖动滑块典型时长）
 * 4. 步数 100-140（接近 125Hz 鼠标采样率）
 * 5. 位置剖面 10·t³ - 15·t⁴ + 6·t⁵（最小急动度，平滑钟形速度）
 * 6. 随机游走噪声 ±1.2px（累积型，频谱宽带，不被 ML 频谱分析识别）
 * 7. X 强制不回退（Baxia 对 X 回退敏感，真实拖动 X 单调递增）
 * 8. 1-2 次微停顿 30-80ms（模拟真人拖动时的犹豫）
 * 9. 释放前 Y 上移 2px + 释放后鼠标移开（人类松手后的自然动作）
 * 10. 接近轨迹 + 按下停顿 + 终点过冲回退
 */
async function humanPhysicsDrag(
  page: Page,
  frame: any,
  button: any,
  startX: number,
  startY: number,
  distance: number,
  attempt: number = 1
): Promise<void> {
  // 起始位置加入微小噪声（人类无法精准定位到像素级）
  const sx = startX + (Math.random() - 0.5) * 1.0;
  const sy = startY + (Math.random() - 0.5) * 1.0;
  // 距离加入 ±1px 噪声（Baxia 精度要求 ±1-2px）
  const dist = distance + (Math.random() - 0.5) * 2;

  // 总时长 0.7-1.3 秒（人类拖动滑块典型时长）
  const total_time_ms = 700 + Math.random() * 600;
  // 步数 100-140（接近 125Hz 鼠标采样率）
  const steps = 100 + Math.floor(Math.random() * 41);
  const avg_delay = total_time_ms / steps;

  // Y 轴慢漂移参数（模拟手部整体移动趋势）
  const yDriftAmp = 2.0 + Math.random() * 3.0;      // 2-5px 漂移幅度
  const yDriftPhase = Math.random() * Math.PI * 2;
  // 抖动幅度（0.5-1.5px，随机噪声非正弦波）
  const tremorAmp = 0.5 + Math.random() * 1.0;
  // 随机游走位置噪声（累积型，频谱宽带，不被 ML 频谱分析识别）
  let noiseAccum = 0.0;
  // 微停顿：1-2 次随机停顿（30-80ms）
  const pauseCount = 1 + Math.floor(Math.random() * 2);
  const pausePositions = [0.15, 0.35, 0.55, 0.75]
    .sort(() => Math.random() - 0.5)
    .slice(0, Math.min(pauseCount, 4))
    .sort((a, b) => a - b);
  let pauseIdx = 0;
  // 过冲参数（2-6px 过冲后修正，人类自然行为）
  const overshootPx = 2 + Math.random() * 4;
  const overshootPauseMs = 40 + Math.random() * 60;

  console.log(`[SliderSolver] humanPhysicsDrag 入口: startX=${sx.toFixed(1)}, startY=${sy.toFixed(1)}, distance=${dist.toFixed(1)}, attempt=${attempt}, steps=${steps}, totalMs=${total_time_ms.toFixed(0)}`);

  // 1. 接近轨迹：从按钮附近随机点移入
  const approachAngle = Math.random() * Math.PI * 2;
  const approachDist = 30 + Math.random() * 60;
  const approachX = sx + Math.cos(approachAngle) * approachDist;
  const approachY = sy + Math.sin(approachAngle) * approachDist;
  await page.mouse.move(approachX, approachY, { steps: 6 });
  await page.waitForTimeout(80 + Math.random() * 120);
  // 移动到按钮
  const approachSteps = 3 + Math.floor(Math.random() * 3);
  for (let i = 1; i <= approachSteps; i++) {
    const t = i / approachSteps;
    const eased = t * t * (3 - 2 * t);
    await page.mouse.move(
      approachX + (sx - approachX) * eased,
      approachY + (sy - approachY) * eased,
      { steps: 4 },
    );
    await page.waitForTimeout(10 + Math.random() * 20);
  }
  // 移动到按钮后的"思考"停顿（100-250ms）
  await page.waitForTimeout(100 + Math.random() * 150);

  // 2. 鼠标按下（CDP 带 deltaX/deltaY，修复 movementX=0 机器人特征）
  const mouse = await createDragMouse(page, sx, sy);
  await mouse.down(sx, sy);
  await page.waitForTimeout(80 + Math.random() * 100);
  // 按下后微小漂移（真人按下到开始拖动之间鼠标常有 1-2px 漂移）
  await mouse.move(sx + (Math.random() - 0.5) * 3, sy + (Math.random() - 0.5) * 3, 3);
  await page.waitForTimeout(30 + Math.random() * 50);

  // 3. 分多步拖动，使用最小急动度位置剖面 + 随机游走噪声 + 强制不回退
  // jerk_pos(t) = 10·t³ - 15·t⁴ + 6·t⁵，t∈[0,1]
  // 这是人类 reaching 运动的最优模型，产生平滑的钟形速度曲线
  let lastX = sx;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    // 最小急动度位置剖面
    const jerkPos = 10 * t * t * t - 15 * t * t * t * t + 6 * t * t * t * t * t;
    // 随机游走位置噪声（累积型，±0.12/步，钳制 ±1.2px）
    noiseAccum += (Math.random() - 0.5) * 0.24;
    noiseAccum = Math.max(-1.2, Math.min(1.2, noiseAccum));
    let targetX = sx + dist * jerkPos + noiseAccum;
    // 确保 X 不回退（Baxia 对 X 回退敏感，真实拖动 X 单调递增）
    if (targetX < lastX) {
      targetX = lastX + 0.1 + Math.random() * 0.4;
    }
    // X 轴微小噪声（人类手指无法完美直线移动）
    targetX += (Math.random() - 0.5) * 0.4;

    // Y 轴：慢漂移 + 随机抖动（减速段抖动逐渐减小到 50%）
    const yDrift = Math.sin(t * Math.PI + yDriftPhase) * yDriftAmp * 0.3;
    let yTremor = (Math.random() - 0.5) * 2 * tremorAmp;
    if (t > 0.7) {
      yTremor *= (1.0 - ((t - 0.7) / 0.3) * 0.5);
    }
    const targetY = sy + yDrift + yTremor;

    await mouse.move(targetX, targetY, 1);

    // 事件间隔：接近 125Hz，加入随机波动
    const delay = Math.max(3, avg_delay + (Math.random() - 0.5) * 7);
    await page.waitForTimeout(delay);

    lastX = targetX;

    // 微停顿（1-2 次，30-80ms）
    if (pauseIdx < pausePositions.length && t >= pausePositions[pauseIdx]) {
      pauseIdx++;
      const pauseMs = 30 + Math.random() * 50;
      const pauseSteps = Math.max(1, Math.floor(pauseMs / 30));
      for (let p = 0; p < pauseSteps; p++) {
        const pDrift = Math.sin(t * Math.PI + yDriftPhase) * yDriftAmp * 0.3;
        await mouse.move(
          lastX + (Math.random() - 0.5) * 0.8,
          sy + pDrift + (Math.random() - 0.5) * 2 * tremorAmp * 0.6,
          1,
        );
        await page.waitForTimeout(25 + Math.random() * 10);
      }
    }
  }

  // 4. 终点过冲后回退（人类拖动常见行为：滑过头再退回来）
  const endX = sx + dist;
  await mouse.move(endX + overshootPx, sy + (Math.random() - 0.5) * 2, 1);
  await page.waitForTimeout(overshootPauseMs);
  // 修正回终点（2-3 次微调）
  for (let a = 0; a < 2 + Math.floor(Math.random() * 2); a++) {
    await mouse.move(endX + (Math.random() - 0.5) * 1.6, sy + (Math.random() - 0.5) * 2, 1);
    await page.waitForTimeout(40 + Math.random() * 80);
  }

  // 5. 释放前：Y 轴微微上移（人类释放时手会自然抬起）
  await mouse.move(endX + (Math.random() - 0.5) * 1, sy - 2 + (Math.random() - 0.5) * 2, 1);
  await page.waitForTimeout(150 + Math.random() * 200);
  // 鼠标释放
  await mouse.up(endX + (Math.random() - 0.5) * 1, sy - 2 + (Math.random() - 0.5) * 2);
  await page.waitForTimeout(80 + Math.random() * 120);

  // 6. 释放后：鼠标自然移开（模拟人类松手后移开）
  for (let i = 0; i < 3; i++) {
    const awayX = endX + 20 + i * 15 + (Math.random() - 0.5) * 16;
    const awayY = sy + 10 + (Math.random() - 0.5) * 30;
    await page.mouse.move(awayX, awayY, { steps: 1 });
    await page.waitForTimeout(30 + Math.random() * 40);
  }
}

/**
 * 检测滑块验证是否通过
 */
async function checkSolved(page: Page, frame: any): Promise<boolean> {
  // 假成功防护：页面加载失败（chrome-error://）时 document.body 为空，
  // 会导致 detect_captcha_container 返回 False + checkSolved 返回 True → 误判"验证通过"（假成功）。
  // 必须在判定"通过"前检查 page.url 是否含 chrome-error:// 或 chromewebdata。
  if (pageShowsLoadFailure(page)) {
    console.warn('[SliderSolver] 检测到页面加载失败（chrome-error://），判定为未通过（防止假成功）');
    return false;
  }

  // 成功标识
  const successSelectors = ['.nc_ok', '.success', '#nc_1_n1z.success', '.icon-success'];
  for (const sel of successSelectors) {
    try {
      const elem = await (frame || page).$(sel);
      if (elem && await elem.isVisible()) {
        return true;
      }
    } catch {
      // ignore
    }
  }

  // 失败/重试标识
  const failSelectors = ['.nc_error', '.errloading', '.fail', '#nc_1_refresh1'];
  for (const sel of failSelectors) {
    try {
      const elem = await (frame || page).$(sel);
      if (elem && await elem.isVisible()) {
        return false;
      }
    } catch {
      // ignore
    }
  }

  // 关键修复（2026-07-31 事故）：Baxia 验证中的等待逻辑
  // 原先拖动后只等待 2 秒就检测，但 Baxia 验证可能需要 3-5 秒。
  // 如果滑块弹窗仍在（验证中），原先直接返回 false（未通过），
  // 然后 checkClickToRetry 误判为失败（因为 .nc-lang-cnt 可见），
  // 进入重试循环，5 次重试全部浪费在"验证中"状态。
  // 修复后：如果滑块弹窗仍在，等待 3 秒后再次检测，最多重试 3 次（共 9 秒）。
  for (let waitRound = 0; waitRound < 3; waitRound++) {
    const stillHasCaptcha = await detectCaptcha(page);
    if (!stillHasCaptcha.detected) {
      // 滑块弹窗消失，视为通过
      return true;
    }
    // 等待 3 秒后再次检测
    if (waitRound < 2) {
      await page.waitForTimeout(3000);
    }
  }
  // 3 次检测后滑块弹窗仍在，判定为未通过
  return false;
}

// ============================================================
// 场景适配：处理加载转圈、点击框体重试、下载消息失败
// ============================================================

/**
 * 等待弹窗状态稳定（处理场景3：加载转圈）
 *
 * 用户反馈：弹窗出现后内部显示转圈动画，持续约3秒后才加载滑块。
 * 旧逻辑固定等待3秒后检测，转圈未完成时匹配不到滑块元素 → 误报"已通过"。
 *
 * 本函数轮询等待，直到以下任一状态：
 * - 滑块按钮就绪（加载完成）→ 返回 detected + sliderReady=true
 * - 连续2次未检测到弹窗容器 → 进入三次确认（等待2秒后再次检测，覆盖转圈场景）
 * - 三次确认仍未检测到 → 返回 detected=false
 * - 超时 → 返回最后检测到的状态
 */
async function waitForCaptchaStable(
  page: Page,
  maxWaitMs: number = 8000
): Promise<{ detected: boolean; selector?: string; iframe?: any; sliderReady: boolean }> {
  const pollInterval = 500;
  const maxPolls = Math.ceil(maxWaitMs / pollInterval);
  let lastResult: { detected: boolean; selector?: string; iframe?: any } = { detected: false };
  let noCaptchaStreak = 0;
  let tripleConfirmUsed = false;

  for (let i = 0; i < maxPolls; i++) {
    const result = await detectCaptcha(page);
    lastResult = result;

    if (result.detected) {
      noCaptchaStreak = 0;
      // 弹窗容器存在，检查滑块按钮是否已加载
      const frame = result.iframe || page.mainFrame();
      const sliderInfo = await getSliderInfo(frame);
      if (sliderInfo) {
        return { ...result, sliderReady: true };
      }
      // 滑块按钮未就绪（可能正在转圈），继续等待
    } else {
      noCaptchaStreak++;
      if (noCaptchaStreak >= 2) {
        // 三次确认：等待2秒后再次检测，覆盖"弹窗正在加载转圈"的场景
        if (!tripleConfirmUsed) {
          console.log('[SliderSolver] 连续2次未检测到弹窗，等待2秒三次确认（可能正在加载转圈）...');
          tripleConfirmUsed = true;
          await page.waitForTimeout(2000);
          const finalCheck = await detectCaptcha(page);
          if (finalCheck.detected) {
            console.log('[SliderSolver] 三次确认检测到弹窗，继续等待滑块就绪');
            lastResult = finalCheck;
            noCaptchaStreak = 0;
            // 继续轮询等待滑块按钮就绪
          } else {
            console.log('[SliderSolver] 三次确认仍未检测到弹窗，判定为无弹窗');
            return { detected: false, sliderReady: false };
          }
        } else {
          return { detected: false, sliderReady: false };
        }
      }
    }
    await page.waitForTimeout(pollInterval);
  }
  return { ...lastResult, sliderReady: false };
}

/**
 * 检测滑动失败后是否需要"点击框体重试"（处理场景2）
 *
 * 用户反馈：滑动后提示"验证失败，点击框体重试(error:HxnXjf)"，
 * 需要点击弹窗触发新滑块，再滑动一次才能成功。
 */
async function checkClickToRetry(
  page: Page,
  frame: any
): Promise<{ needsClick: boolean; retryTarget?: any }> {
  // 1. 检测失败/重试标识元素（Baxia 标准类名）
  // 关键修复（2026-07-31 事故）：移除 .nc-lang-cnt
  // .nc-lang-cnt 是滑块按钮本身的容器（在 getSliderInfo 的 buttonSelectors 中也包含它），
  // 不是失败标识。原先把它放在 retrySelectors 中，导致每次拖动后只要滑块按钮还在
  // （Baxia 验证中），就会误判为"检测到失败提示"，进入重试循环，5 次重试全部浪费。
  // 修复后：只有明确的失败标识（.nc_error/.errloading/#nc_1_refresh1/.fail）才触发重试。
  const retrySelectors = [
    '.nc_error',
    '.errloading',
    '#nc_1_refresh1',
    '.fail',
  ];

  const searchFrames: any[] = [];
  if (frame) searchFrames.push(frame);
  for (const f of page.frames()) {
    if (f === page.mainFrame() || f === frame) continue;
    searchFrames.push(f);
  }
  if (!searchFrames.includes(page.mainFrame())) searchFrames.push(page.mainFrame());

  for (const f of searchFrames) {
    if (!f) continue;
    for (const sel of retrySelectors) {
      try {
        const elem = await f.$(sel);
        if (elem && await elem.isVisible()) {
          console.log(`[SliderSolver] checkClickToRetry 命中选择器: ${sel}`);
          return { needsClick: true, retryTarget: elem };
        }
      } catch { /* ignore */ }
    }
  }

  // 2. 检测"验证失败，点击框体重试"文本
  // 关键修复（2026-07-31 事故）：收紧文本匹配，避免误判
  // 原先的正则 /error:/ 会匹配到 URL 参数、JS 错误信息等非失败文本，
  // /请重试/ 会匹配到"请稍后重试"等非滑块失败的提示。
  // 修复后：只匹配明确的滑块验证失败文本，且要求文本长度较短（<200字符），
  // 避免匹配到页面正文中的无关内容。
  for (const f of searchFrames) {
    if (!f) continue;
    try {
      const hasRetryText = await f.evaluate(() => {
        const text = document.body ? document.body.innerText : '';
        // 只在文本较短（<200字符）时检测，避免匹配到页面正文
        if (text.length > 200) return false;
        // 覆盖多种失败提示文本：
        // - "验证失败，点击框体重试(error:HxnXjf)"
        // - "滑块加载失败"
        // - "滑动失败"
        // - "验证未通过"
        return /验证失败.*点击框体重试|点击框体重试|滑块加载失败|滑动失败|验证未通过/i.test(text);
      }).catch(() => false);
      if (hasRetryText) {
        console.log(`[SliderSolver] checkClickToRetry 命中失败文本`);
        // 找到可点击的弹窗容器
        for (const sel of ['#nc_1', '.nc_wrapper', '#baxia-dialog', '.nc-lang-cnt', '.slide-verify']) {
          try {
            const elem = await f.$(sel);
            if (elem && await elem.isVisible()) {
              return { needsClick: true, retryTarget: elem };
            }
          } catch { /* ignore */ }
        }
        return { needsClick: true, retryTarget: undefined };
      }
    } catch { /* ignore */ }
  }
  return { needsClick: false };
}

/**
 * 点击弹窗触发新滑块（处理"验证失败，点击框体重试"场景）
 */
async function clickRetryToResetSlider(page: Page, frame: any, target: any): Promise<void> {
  if (target) {
    try {
      await target.click();
      return;
    } catch { /* ignore */ }
  }
  // 兜底：点击弹窗中心区域
  const searchFrames: any[] = frame ? [frame] : [page.mainFrame()];
  for (const f of searchFrames) {
    try {
      const box = await f.evaluate(() => {
        const sel = '#nc_1, .nc_wrapper, #baxia-dialog, .nc-lang-cnt, .slide-verify';
        const elem = document.querySelector(sel);
        if (elem) {
          const rect = elem.getBoundingClientRect();
          return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
        }
        return null;
      }).catch(() => null);
      if (box) {
        await page.mouse.click(box.x, box.y);
        return;
      }
    } catch { /* ignore */ }
  }
}

/**
 * 检测页面是否已跳转到登录页（Cookie Session 过期）
 *
 * 闲鱼消息页在 Cookie 过期时会通过 JS 路由跳转到登录页，
 * 但跳转可能需要几秒（SPA 路由），page.goto 后立即检测可能漏检。
 * 本函数检测 URL 和页面文本特征，用于在"未检测到滑块"时再次确认。
 */
async function checkLoginPage(page: Page): Promise<boolean> {
  // 1. 检测 URL 跳转
  const currentUrl = page.url();
  if (/login\.taobao\.com|login\.goofish\.com|\/login\b|\/uiLogin\b/i.test(currentUrl)) {
    return true;
  }
  // 2. 检测登录页文本特征（包含"扫码登录"等且不含商品列表关键字）
  try {
    const hasLoginIndicator = await page.evaluate(() => {
      const text = document.body ? document.body.innerText : '';
      if (/扫码登录|手机号登录|账号密码登录/i.test(text) && !/我想要|猜你喜欢|闲置/i.test(text)) {
        return true;
      }
      return false;
    }).catch(() => false);
    if (hasLoginIndicator) return true;
  } catch {
    // ignore
  }
  return false;
}

/**
 * 查找并点击滑块弹窗的关闭按钮（叉号）
 *
 * 模拟真人在连续滑动失败后的行为：点击弹窗右上角的叉号关闭弹窗。
 * 用户反馈：连续滑动失败后，真人会点击叉号关闭弹窗→刷新页面→重新滑动，
 * 成功率大幅提升。本函数实现"点击叉号关闭弹窗"这一步。
 */
async function closeCaptchaDialog(page: Page): Promise<boolean> {
  // 关闭按钮的候选选择器
  const closeSelectors = [
    // Baxia 弹窗专用关闭按钮
    '.nc_close', '.nc-icon-close', '.baxia-close',
    // 通用弹窗关闭按钮
    '.dialog-close', '.modal-close', '.popup-close',
    '.close-btn', '.btn-close',
    // Ant Design / Next UI 关闭按钮
    '.ant-modal-close', '.next-dialog-close',
    // 通用 close 类名和 aria-label
    '[class*="close"][role="button"]',
    'button[aria-label*="close"]',
    'button[aria-label*="关闭"]',
    // SVG 图标按钮
    '.icon-close', '[class*="icon-close"]',
  ];

  const searchFrames: any[] = [page.mainFrame(), ...page.frames().filter(f => f !== page.mainFrame())];

  for (const f of searchFrames) {
    if (!f) continue;
    for (const sel of closeSelectors) {
      try {
        const elem = await f.$(sel);
        if (elem && await elem.isVisible()) {
          console.log(`[SliderSolver] 找到弹窗关闭按钮: ${sel}`);
          await elem.click({ timeout: 2000 }).catch(() => {});
          await page.waitForTimeout(800);  // 等待弹窗关闭动画
          return true;
        }
      } catch { /* ignore */ }
    }

    // 兜底：查找包含 × 或 "关闭" 文本的可点击元素
    try {
      const closed = await f.evaluate(() => {
        const candidates: Element[] = [];
        const allElems = document.querySelectorAll('button, [role="button"], a, span, div, i, svg');
        for (const el of Array.from(allElems)) {
          const text = (el.textContent || '').trim();
          const ariaLabel = el.getAttribute('aria-label') || '';
          const className = el.className || '';
          const classNameStr = typeof className === 'string' ? className : '';
          // 匹配 × ✕ ✗ x 关闭 close 等特征
          if (/^[×✕✗xX]$/.test(text) ||
              /关闭|close/i.test(ariaLabel) ||
              /close|关闭/i.test(classNameStr)) {
            const rect = el.getBoundingClientRect();
            // 只考虑尺寸较小的元素（按钮大小，不是大块内容）
            if (rect.width > 0 && rect.width < 60 && rect.height > 0 && rect.height < 60) {
              candidates.push(el);
            }
          }
        }
        // 优先选择右上角的元素（关闭按钮通常在右上角）
        candidates.sort((a, b) => {
          const rectA = a.getBoundingClientRect();
          const rectB = b.getBoundingClientRect();
          // 按 x 坐标降序（右优先），y 坐标升序（上优先）
          return (rectB.right - rectA.right) || (rectA.top - rectB.top);
        });
        if (candidates.length > 0) {
          (candidates[0] as HTMLElement).click();
          return true;
        }
        return false;
      }).catch(() => false);
      if (closed) {
        console.log(`[SliderSolver] 通过文本特征点击了弹窗关闭按钮`);
        await page.waitForTimeout(800);
        return true;
      }
    } catch { /* ignore */ }
  }

  console.log('[SliderSolver] 未找到弹窗关闭按钮');
  return false;
}

/**
 * 检测多次失败后弹出的"刷新页面"/"连接中断"小弹窗（处理场景5、场景6）
 *
 * 用户反馈两种需要刷新的小弹窗：
 * - 场景5：多次"验证失败，点击框体重试"后弹出"刷新页面"小弹窗
 * - 场景6：滑块弹窗后又弹出"连接中断，请重连"弹窗（两个弹窗共存）
 *
 * 检测到任一弹窗后，直接刷新页面，然后重新尝试滑块验证。
 *
 * 特征：通常是一个模态对话框，包含"刷新"/"重新加载"/"连接中断"/"请重连"等文本。
 */
async function checkRefreshDialog(page: Page): Promise<boolean> {
  // 判断 frame 是否应该被检测"刷新/连接中断"弹窗
  // 排除：Baxia punish frame（_____tmd_____/punish）、第三方脚本 frame（非 goofish.com）
  // 原因：Baxia punish frame 的 nocaptcha/nc.js 脚本中包含"重新加载"、"刷新"等词，
  //       会导致误判为"刷新弹窗"，从而在拖动前一直刷新页面，永远没机会拖动滑块。
  const isCheckableFrame = (f: any): boolean => {
    try {
      const url: string = f.url() || '';
      if (!url) return false;
      // 排除 Baxia punish / 验证码 frame
      if (/_____tmd_____|punish|baxia|nocaptcha|captcha|verify/i.test(url)) return false;
      // 只检测 goofish.com 主 frame（IM 页面），排除第三方脚本 frame
      if (!/goofish\.com/i.test(url)) return false;
      return true;
    } catch {
      return false;
    }
  };

  const checkFrame = async (f: any) => {
    try {
      return await f.evaluate(() => {
        // 严格检测：必须有可见的 modal/dialog 元素，且元素内有明确的"刷新"按钮文本
        // 不再检测整个 body 的 innerText，避免误判闲鱼 IM 页面的 WebSocket 状态提示（如"连接中断"）
        // 误判根因：闲鱼 IM 页面在风控触发时会显示"连接中断"等 WebSocket 状态提示，
        //          但这只是连接状态，不是真的需要刷新页面，滑块验证仍然可以进行。
        const modalSelectors = [
          '.ant-modal-confirm', '.ant-modal-body', '.ant-modal',
          '.next-dialog', '.next-dialog-message',
          '.refresh-dialog', '.modal-refresh', '.dialog-refresh',
          '[role="dialog"]', '[class*="modal"]', '[class*="dialog"]',
        ];
        for (const sel of modalSelectors) {
          const elems = document.querySelectorAll(sel);
          for (const elem of elems) {
            // 必须可见
            const rect = elem.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(elem);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
            // 元素文本必须包含明确的"刷新页面"指令（不是"连接中断"等状态提示）
            const elemText = (elem.textContent || '').trim();
            if (elemText.length === 0) continue;
            // 只匹配明确的"请刷新页面"指令，不匹配"连接中断"等状态提示
            if (/请刷新页面|刷新重试|刷新页面后重试|网络异常.*请刷新|页面已失效.*请刷新|刷新后重试/i.test(elemText)) {
              return true;
            }
          }
        }
        return false;
      }).catch(() => false);
    } catch {
      return false;
    }
  };

  // 只检测 goofish.com 主 frame，排除 Baxia punish frame
  if (isCheckableFrame(page) && await checkFrame(page)) return true;
  for (const f of page.frames()) {
    if (f === page.mainFrame()) continue;
    if (!isCheckableFrame(f)) continue;
    if (await checkFrame(f)) return true;
  }
  return false;
}

/**
 * 检测滑块通过后是否出现"下载消息失败"（处理场景4）
 *
 * 用户反馈：滑动完成后提示下载消息失败，要求刷新页面后再次滑动滑块。
 */
async function checkDownloadMessageFailed(page: Page): Promise<boolean> {
  const checkFrame = async (f: any) => {
    try {
      return await f.evaluate(() => {
        const text = document.body ? document.body.innerText : '';
        return /下载消息失败|消息加载失败|加载失败.*请刷新|请刷新.*重新加载/i.test(text);
      }).catch(() => false);
    } catch {
      return false;
    }
  };

  if (await checkFrame(page)) return true;
  for (const f of page.frames()) {
    if (f === page.mainFrame()) continue;
    if (await checkFrame(f)) return true;
  }
  return false;
}

/**
 * 模拟真人导航到消息页（先访问首页 → 点击"消息"按钮 → 捕获新窗口的消息页）
 *
 * 关键修复：直接 page.goto('https://www.goofish.com/im') 会被闲鱼反爬识别为
 * 非正常用户行为，导致消息页显示"加载失败"提示。真人访问路径是：打开闲鱼
 * 首页 → 点击侧边栏的"消息"按钮，通过站内路由进入消息页，这样不会触发反爬。
 *
 * 关键修复：闲鱼的"消息"链接是 target="_blank"，点击会在新窗口打开。
 * 采用双窗口方案：第一个窗口（homePage）停在首页不刷新，
 * 第二个窗口（popup）是消息页，所有滑块操作在 popup 上进行。
 */
async function navigateToMessagePage(homePage: Page, timeoutMs: number): Promise<Page | undefined> {
  const homeUrl = 'https://www.goofish.com';

  console.log(`[SliderSolver] 模拟真人导航：先访问首页 ${homeUrl}`);
  await homePage.goto(homeUrl, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
  // 等待首页 SPA 渲染完成（侧边栏渲染需要时间）
  await homePage.waitForTimeout(2000);
  console.log(`[SliderSolver] 首页加载完成，当前 URL: ${homePage.url()}`);

  // 查找"消息"入口并点击（多种 selector 兜底，按优先级尝试）
  // 关键：每次策略点击前重新设置 popup 监听，避免 promise 被消耗
  const strategies: Array<{ name: string; run: () => Promise<boolean> }> = [
    // 策略1（最可靠）：evaluate 调用原生 DOM click()，绕过 Playwright 反爬检测
    {
      name: 'evaluate 原生 click',
      run: async () => {
        return await homePage.evaluate(() => {
          const wraps = Array.from(document.querySelectorAll('[class*="sidebar-item-wrap"]'));
          const target = wraps.find((w) => {
            const t = (w.textContent || '').trim();
            return t.includes('消息');
          });
          if (target) {
            (target as HTMLElement).scrollIntoView({ block: 'center' });
            (target as HTMLElement).click();
            return true;
          }
          const sideAreas = Array.from(document.querySelectorAll('[class*="sidebar"], aside, [class*="side-nav"]'));
          for (const area of sideAreas) {
            const nodes = Array.from(area.querySelectorAll('a, button, [role="link"], [role="button"]'));
            const t = nodes.find((n) => {
              const txt = (n.textContent || '').trim();
              return txt.includes('消息');
            });
            if (t) {
              (t as HTMLElement).scrollIntoView({ block: 'center' });
              (t as HTMLElement).click();
              return true;
            }
          }
          return false;
        }).catch(() => false);
      },
    },
    // 策略2：Playwright click(force: true) 强制点击 sidebar-item-wrap 元素
    {
      name: 'sidebar-item-wrap force click',
      run: async () => {
        const loc = homePage.locator('[class*="sidebar-item-wrap"]')
          .filter({ hasText: '消息' })
          .first();
        if (await loc.count() > 0) {
          await loc.click({ timeout: 3000, force: true }).catch(() => {});
          return true;
        }
        return false;
      },
    },
    // 策略3：dispatchEvent 派发 MouseEvent
    {
      name: 'dispatchEvent MouseEvent',
      run: async () => {
        return await homePage.evaluate(() => {
          const wraps = Array.from(document.querySelectorAll('[class*="sidebar-item-wrap"]'));
          const target = wraps.find((w) => {
            const t = (w.textContent || '').trim();
            return t.includes('消息');
          });
          if (target) {
            const rect = (target as HTMLElement).getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            for (const type of ['mousedown', 'mouseup', 'click']) {
              const event = new MouseEvent(type, {
                bubbles: true,
                cancelable: true,
                view: window,
                clientX: x,
                clientY: y,
              });
              target.dispatchEvent(event);
            }
            return true;
          }
          return false;
        }).catch(() => false);
      },
    },
  ];

  for (const strategy of strategies) {
    try {
      // 每次策略点击前重新设置 popup 监听（5 秒超时）
      // 关键：popupPromise 是一次性的，一旦 resolve/reject 就不能再用。
      const popupPromise = homePage.waitForEvent('popup', { timeout: 5000 }).catch(() => null);
      const ok = await strategy.run();
      if (!ok) continue;
      // 点击后等待 popup（新窗口）打开
      const popup = await popupPromise;
      if (popup) {
        // 等待新窗口加载完成
        await popup.waitForLoadState('domcontentloaded', { timeout: timeoutMs }).catch(() => {});
        console.log(`[SliderSolver] 策略 "${strategy.name}" 成功打开消息页新窗口，URL: ${popup.url()}`);
        // 等待消息页 SPA 完全渲染 + 滑块弹窗加载
        await popup.waitForTimeout(3000);
        return popup;
      }
      console.warn(`[SliderSolver] 策略 "${strategy.name}" 点击后未捕获到新窗口，尝试下一策略`);
    } catch {
      // ignore，尝试下一策略
    }
  }

  // 所有策略都失败，回退到直接访问 /im（在原窗口操作）
  console.warn('[SliderSolver] 所有策略均未打开新窗口，回退到直接访问 /im');
  await homePage.goto('https://www.goofish.com/im', {
    waitUntil: 'domcontentloaded',
    timeout: timeoutMs,
  });
  await homePage.waitForTimeout(1500);
  return homePage;
}

/**
 * 主入口：启动浏览器、检测滑块、自动拖动
 *
 * 增强方案：
 * 1. 优先使用真实 Chrome 持久化上下文（避免 remote-debugging-port 二次挂载）
 * 2. 注入反检测脚本（webdriver/chrome/plugins/WebGL/Canvas 指纹）
 * 3. 模拟真人导航（首页→点击消息→新窗口）
 * 4. 多场景处理（加载转圈、点击框体重试、刷新弹窗、下载消息失败、登录页跳转）
 * 5. 真人行动模拟（连续失败后关闭弹窗→刷新页面→冷静期→重新尝试）
 */
export async function solveGoofishSlider(options: SlideSolveOptions = {}): Promise<SlideSolveResult> {
  const startTime = Date.now();
  const targetUrl = options.targetUrl || DEFAULT_TARGET_URL;
  const headless = resolveHeadlessMode(options.headless);
  // 默认重试 5 次（与商业版对齐，强化本地求解能力）
  const retries = Number(options.maxRetries ?? 5);
  const maxRetries = Number.isSafeInteger(retries) ? Math.max(1, Math.min(retries, 10)) : 5;
  const timeout = Number(options.timeoutMs ?? 30000);
  const timeoutMs = Number.isSafeInteger(timeout) ? Math.max(5000, Math.min(timeout, 180000)) : 30000;

  let browser: Browser | null = null;
  let context: any = null;
  let screenshotPath: string | undefined;
  // 持久化上下文的 userDataDir，需在 finally 中清理以防 Cookie/缓存残留磁盘
  let userDataDirForCleanup: string | null = null;
  const abortBrowser = () => {
    void browser?.close().catch(() => undefined);
  };

  try {
    options.signal?.throwIfAborted();
    const contextOptions: BrowserContextOptions = {
      viewport: { width: 1280, height: 800 },
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      locale: 'zh-CN',
    };

    if (options.cookieStr) {
      const cookies = parseCookieString(options.cookieStr);
      contextOptions.storageState = { cookies, origins: [] };
    }

    // === 方案A：真实 Chrome 持久化上下文（优先），避免 remote-debugging-port 二次挂载 ===
    let usingCDP = false;
    const chromePaths = [
      'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
      'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
      `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`,
    ];
    const chromePath = chromePaths.find(p => {
      try { return fsSync.existsSync(p); } catch { return false; }
    });

    // 优先：真实 Chrome + 持久化配置，且不二次 remote-debugging-port 挂载
    // （remote-debugging-port 是「自动化窗口」被闲鱼标记、人工拖也加载失败的强信号之一）
    if (chromePath && !headless) {
      try {
        const userDataDir = path.join(process.env.TEMP || '/tmp', `chrome-slider-warm-${Date.now()}`);
        userDataDirForCleanup = userDataDir;
        await fs.mkdir(userDataDir, { recursive: true });
        console.log(`[SliderSolver] launchPersistentContext 真实 Chrome: ${chromePath}`);
        context = await chromium.launchPersistentContext(userDataDir, {
          headless: false,
          executablePath: chromePath,
          viewport: { width: 1366, height: 768 },
          locale: 'zh-CN',
          timezoneId: 'Asia/Shanghai',
          userAgent:
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
          ignoreDefaultArgs: ['--enable-automation'],
          args: [
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-popup-blocking',
            '--window-size=1366,768',
            '--disable-blink-features=AutomationControlled',
            '--lang=zh-CN',
          ],
        });
        // launchPersistentContext 返回 BrowserContext，其 browser() 可用于关闭
        browser = context.browser();
        if (options.cookieStr) {
          await context.addCookies(parseCookieString(options.cookieStr));
        }
        await context.addInitScript(ANTI_DETECT_SCRIPT);
        usingCDP = true;
        console.log('[SliderSolver] 已启动持久化 Chrome 并注入反检测脚本');
      } catch (e: any) {
        console.warn(`[SliderSolver] 持久化 Chrome 启动失败，回退: ${safeErrorType(e)}`);
        if (browser) { await browser.close().catch(() => {}); browser = null; }
        context = null;
      }
    }

    // === 方案B：回退到 Playwright 启动 Chromium ===
    if (!usingCDP || !browser || !context) {
      const disableSandbox = /^(1|true|yes|on)$/i.test(
        String(process.env.PLAYWRIGHT_DISABLE_SANDBOX || '')
      );
      browser = await chromium.launch({
        headless,
        chromiumSandbox: process.platform === 'linux' && !disableSandbox,
        // 去掉 Playwright 默认 --enable-automation，显著降低「自动化窗口」被标记概率
        ignoreDefaultArgs: ['--enable-automation'],
        args: [
          '--disable-blink-features=AutomationControlled',
          '--disable-dev-shm-usage',
          '--no-first-run',
          '--no-default-browser-check',
          ...(disableSandbox ? ['--no-sandbox'] : []),
        ],
      });
      options.signal?.addEventListener('abort', abortBrowser, { once: true });
      options.signal?.throwIfAborted();
      context = await browser.newContext({
        ...contextOptions,
        userAgent:
          contextOptions.userAgent ||
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
        locale: 'zh-CN',
        timezoneId: 'Asia/Shanghai',
      });
      // 反检测：注入完整反检测脚本（替代原 plugins=[1,2,3,4,5] 数字数组的弱实现）
      await context.addInitScript(ANTI_DETECT_SCRIPT);
    }

    let page = await context.newPage();
    // 保存首页窗口引用，刷新时从首页重新点击"消息"按钮打开新窗口
    // 关键：page.reload() 刷新消息页会触发反爬"加载失败"。
    // 改为：关闭旧消息页窗口，从首页重新点击"消息"按钮打开新窗口（真人导航路径）。
    const homePage = page;

    // 访问目标页面
    // 关键修复：直接 page.goto('https://www.goofish.com/im') 会被闲鱼反爬识别为
    // 非正常用户行为，导致消息页显示"加载失败"。改为模拟真人访问路径：
    // 首页 → 点击"消息"按钮 → 消息页新窗口。
    if (!options.targetUrl || options.targetUrl === DEFAULT_TARGET_URL) {
      const messagePage = await navigateToMessagePage(homePage, timeoutMs);
      if (messagePage && messagePage !== homePage) {
        console.log(`[SliderSolver] 已切换到消息页新窗口，URL: ${messagePage.url()}`);
        // 后续所有操作在新窗口（消息页）上进行，原窗口（首页）保持不动
        page = messagePage;
      } else {
        console.log(`[SliderSolver] 未捕获到新窗口，使用原窗口继续`);
      }
    } else {
      console.log(`[SliderSolver] 访问指定目标页面: ${targetUrl}`);
      await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
      // 等待 1.5 秒让页面 JS 重定向完成（登录页跳转等），滑块弹窗的加载转圈由 waitForCaptchaStable 处理
      await page.waitForTimeout(1500);
    }

    // === Cookie 过期检测 ===
    // 关键修复：Cookie Session 过期时闲鱼首页会重定向到登录页（login.taobao.com 或 goofish.com/login），
    // 此时不会出现滑块弹窗，旧逻辑会误报 "solved: true"，导致 captcha_solver 错误地恢复 cookie_status=1。
    // 必须在此检测登录页跳转，明确返回失败，让用户知道需要重新扫码登录而不是滑块问题。
    const currentUrl = page.url();
    const isLoginPage =
      /login\.taobao\.com/i.test(currentUrl) ||
      /login\.goofish\.com/i.test(currentUrl) ||
      /\/login\b/i.test(currentUrl) ||
      /\/uiLogin\b/i.test(currentUrl);
    if (isLoginPage) {
      console.warn('[SliderSolver] 页面被重定向到登录页，Cookie Session 已过期');
      return {
        ok: false,
        solved: false,
        captchaDetected: false,
        attempts: 0,
        error: 'Cookie Session 已过期，页面被重定向到登录页，请重新扫码登录闲鱼账号获取新 Cookie',
        durationMs: Date.now() - startTime,
      };
    }

    // 检测页面是否显示登录入口（部分场景下登录页是 SPA 内嵌，URL 不变化）
    const hasLoginIndicator = await page.evaluate(() => {
      const text = document.body ? document.body.innerText : '';
      // 登录页特征：包含"扫码登录"或"手机号登录"且不含商品列表关键字
      if (/扫码登录|手机号登录|账号密码登录/i.test(text) && !/我想要|猜你喜欢|闲置/i.test(text)) {
        return true;
      }
      return false;
    }).catch(() => false);
    if (hasLoginIndicator) {
      console.warn(`[SliderSolver] 页面显示登录入口，Cookie Session 已过期`);
      return {
        ok: false,
        solved: false,
        captchaDetected: false,
        attempts: 0,
        error: 'Cookie Session 已过期，页面显示登录入口，请重新扫码登录闲鱼账号获取新 Cookie',
        durationMs: Date.now() - startTime,
      };
    }

    let attempts = 0;
    let lastError: string | undefined;
    // 场景4：下载消息失败后需要刷新页面重新验证，限制最多刷新2次
    let downloadRetryCount = 0;
    const MAX_DOWNLOAD_RETRIES = 2;
    let needReloadForDownload = false;
    // 场景5：刷新小弹窗后需要刷新页面重新验证，限制最多刷新5次
    let refreshRetryCount = 0;
    const MAX_REFRESH_RETRIES = 3;
    let needReloadForRefresh = false;
    // 场景2：点击框体重试限制，与 maxRetries 一致，确保每次失败都能点击重试
    let clickRetryCount = 0;
    const MAX_CLICK_RETRIES = 5;
    // 真人行动模拟：连续失败 N 次后触发"关闭弹窗→刷新页面→冷静期→重新尝试"
    // 2026-08-04 对齐商业版参数（HUMAN_ACTION_THRESHOLD=2 / MAX_HUMAN_ACTIONS=1 / 冷静期 7-11s）
    const HUMAN_ACTION_THRESHOLD = 2;  // 连续失败 2 次后触发（与商业版对齐）
    const MAX_HUMAN_ACTIONS = 1;       // 最多触发 1 次真人行动（与商业版对齐）
    let humanActionCount = 0;
    // 真人行动刷新后的冷静期标志：刷新完成后不立即操作，等待 7-11 秒让 Baxia 检测状态自然重置
    let needCooldownAfterHumanAction = false;

    // 使用 while 循环，让场景4(下载消息失败)和场景5(刷新小弹窗)的刷新重试
    // 不占用 maxRetries 的滑动重试配额，否则5次滑动配额会被刷新操作耗尽
    let attempt = 0;
    // 总尝试次数硬上限：防止 attempt-- 回退 + 多种重试叠加导致循环次数超预期
    // 理论最大循环次数 = maxRetries(5) + MAX_REFRESH_RETRIES(3) + MAX_DOWNLOAD_RETRIES(2) + MAX_CLICK_RETRIES(5) = 15
    // 设置 maxRetries * 3 作为硬上限，接近整体超时前必须退出（与商业版对齐）
    const MAX_TOTAL_ATTEMPTS = Math.max(maxRetries * 3, 15);
    let totalAttempts = 0;
    while (attempt < maxRetries) {
      totalAttempts++;
      if (totalAttempts > MAX_TOTAL_ATTEMPTS) {
        console.warn(`[SliderSolver] 总尝试次数已达上限 (${MAX_TOTAL_ATTEMPTS})，强制退出循环`);
        lastError = lastError || `滑块求解总尝试次数超限 (${MAX_TOTAL_ATTEMPTS})`;
        break;
      }
      attempt++;
      attempts = attempt;

      // 场景4/场景5：如果上一轮标记了需要刷新（下载消息失败 或 刷新小弹窗），从首页重新打开消息页
      // 关键修复：page.reload() 刷新消息页窗口会触发反爬"加载失败"。
      // 改为：关闭旧消息页窗口，从首页（homePage）重新点击"消息"按钮打开新窗口。
      if (needReloadForDownload || needReloadForRefresh) {
        const reason = needReloadForDownload ? '下载消息失败' : '刷新小弹窗';
        needReloadForDownload = false;
        needReloadForRefresh = false;
        console.log(`[SliderSolver] 从首页重新打开消息页窗口（${reason}重试）`);
        try {
          // 关键：刷新重试前必须清除 Baxia 在访问过程中通过 Set-Cookie 重新设置的 risk cookies。
          // 初始化时 stripRiskCookies 只清除原始 cookie 中的 risk 字段，但浏览器访问 goofish.com 时
          // Baxia 会重新设置这些标记。带着 risk cookies 重新访问会持续被判定为高风险，
          // 形成"刷新→带 risk cookies→再次 punish→刷新"死循环。
          try {
            const currentCookies = await context.cookies();
            const cleanCookies = currentCookies.filter((c: any) => !RISK_COOKIE_NAMES.includes(c.name));
            const removedCount = currentCookies.length - cleanCookies.length;
            if (removedCount > 0) {
              console.log(`[SliderSolver] 刷新重试前清除 ${removedCount} 个 risk cookies`);
              await context.clearCookies();
              await context.addCookies(cleanCookies);
            }
          } catch (e: any) {
            console.warn(`[SliderSolver] 清除 risk cookies 失败（不影响主流程）: ${safeErrorType(e)}`);
          }
          // 关闭旧的消息页窗口（如果不是首页窗口）
          if (page !== homePage) {
            await page.close().catch(() => {});
          }
          // 从首页重新点击"消息"按钮，打开新的消息页窗口
          const newMessagePage = await navigateToMessagePage(homePage, timeoutMs);
          if (newMessagePage && newMessagePage !== homePage) {
            console.log(`[SliderSolver] 成功重新打开消息页窗口，URL: ${newMessagePage.url()}`);
            page = newMessagePage;
          } else {
            console.warn(`[SliderSolver] 重新打开消息页窗口失败，使用原窗口继续`);
            page = homePage;
          }
        } catch (e: any) {
          console.error(`[SliderSolver] 从首页重新打开消息页失败: ${safeErrorType(e)}`);
        }
      }

      // 真人行动冷静期：刷新完成后不立即操作，等待 7-11 秒让 Baxia 检测状态自然重置
      // 2026-08-04 对齐商业版：原 3-7 秒调整为 7-11 秒
      if (needCooldownAfterHumanAction) {
        needCooldownAfterHumanAction = false;
        const cooldownMs = 7000 + Math.floor(Math.random() * 4000);  // 7-11 秒（与商业版对齐）
        console.log(`[SliderSolver] 真人行动：页面刷新完成，等待 ${cooldownMs}ms 冷静期（不进行任何操作）...`);
        await page.waitForTimeout(cooldownMs);
        console.log(`[SliderSolver] 冷静期结束，开始检测滑块`);
      }

      console.log(`[SliderSolver] 第 ${attempt}/${maxRetries} 次检测滑块...`);

      // 场景3：等待弹窗状态稳定（处理加载转圈，三次确认机制覆盖3秒转圈周期）
      const stable = await waitForCaptchaStable(page, 8000);

      if (!stable.detected) {
        // 二次确认：等待1秒后再检测，避免弹窗仍在加载转圈时误报"已通过"
        console.log('[SliderSolver] 未检测到弹窗，等待1秒二次确认...');
        await page.waitForTimeout(1000);
        const recheck = await detectCaptcha(page);
        if (!recheck.detected) {
          // ★ 关键修复：Cookie 过期时页面可能已跳转到登录页（SPA 路由延迟跳转），
          // goto 后 1.5 秒内可能还没跳转，但此时已检测不到滑块。
          // 必须在"无弹窗"时再次检测登录页，避免误报"已通过"导致 cookie_status 错误恢复。
          // 假成功防护：页面加载失败（chrome-error://）时 document.body 为空，
          // detectCaptcha 返回 false 会被误判为"已通过"。必须先检测页面加载状态。
          if (pageShowsLoadFailure(page)) {
            console.warn('[SliderSolver] 二次确认时检测到页面加载失败（chrome-error://），触发刷新重试');
            if (refreshRetryCount < MAX_REFRESH_RETRIES) {
              refreshRetryCount++;
              needReloadForRefresh = true;
              attempt--;
              continue;
            }
          }
          if (await checkLoginPage(page)) {
            console.warn('[SliderSolver] 二次确认时检测到登录页，Cookie Session 已过期');
            return {
              ok: false,
              solved: false,
              captchaDetected: false,
              attempts,
              error: 'Cookie Session 已过期，页面被重定向到登录页，请重新扫码登录闲鱼账号获取新 Cookie',
              durationMs: Date.now() - startTime,
            };
          }
          console.log('[SliderSolver] 确认无滑块，可能已通过验证或不需要验证');
          {
            const cookieStr = await exportContextCookies(context);
            return {
              ok: true,
              solved: true,
              captchaDetected: false,
              attempts,
              durationMs: Date.now() - startTime,
              cookieStr,
            };
          }
        }
        console.log('[SliderSolver] 二次确认检测到弹窗，继续处理');
      }

      // 获取最终检测结果
      const detected = stable.detected ? stable : await detectCaptcha(page);
      if (!detected.detected) {
        {
          const cookieStr = await exportContextCookies(context);
          return {
            ok: true,
            solved: true,
            captchaDetected: false,
            attempts,
            durationMs: Date.now() - startTime,
            cookieStr,
          };
        }
      }

      console.log(`[SliderSolver] 检测到滑块: selector=${detected.selector}`);
      const frame = detected.iframe || page.mainFrame();

      // Punish 状态检测（同步自商业版 sliderSolver.ts）
      // 关键：punish URL（含 _____tmd_____ 或 punish）统一视为 account_punished（可拖动），
      // 不得因 URL 含 login.token 而误判为 cookie_invalid（login.token 是 WS token 刷新 API 名字，
      // 不是 Cookie 失效信号）。Cookie 是否真正失效只能通过 checkLoginPage 检测真实登录页跳转判断。
      // 注意：开源版无 Python patchright fallback，punish 状态下仍必须尝试拖动滑块
      // （拖动是脱离 punish 状态的唯一途径），连续失败由真人行动机制兜底。
      const isPunished = await checkPunishedFrame(frame);
      if (isPunished) {
        console.log(`[SliderSolver] 检测到 Baxia punish 状态，仍尝试拖动滑块（脱离 punish 的唯一途径）`);
      }

      // 场景5/场景6：拖动前持续检测"刷新页面"/"连接中断"小弹窗
      if (await checkRefreshDialog(page)) {
        if (refreshRetryCount < MAX_REFRESH_RETRIES) {
          refreshRetryCount++;
          console.log(
            `[SliderSolver] 拖动前检测到"刷新/连接中断"弹窗，将刷新页面重试 (${refreshRetryCount}/${MAX_REFRESH_RETRIES})`
          );
          needReloadForRefresh = true;
          attempt--;
          continue;
        } else {
          console.warn(`[SliderSolver] 刷新弹窗重试已达上限 (${MAX_REFRESH_RETRIES})`);
        }
      }

      // 等待滑块按钮就绪（处理弹窗已出现但滑块仍在转圈加载的情况）
      let sliderInfo = await getSliderInfo(frame);
      if (!sliderInfo) {
        console.log('[SliderSolver] 滑块按钮未就绪，额外等待加载...');
        for (let w = 0; w < 8 && !sliderInfo; w++) {
          await page.waitForTimeout(500);
          sliderInfo = await getSliderInfo(frame);
          // 等待期间持续检测"刷新/连接中断"弹窗
          if (sliderInfo && await checkRefreshDialog(page)) {
            if (refreshRetryCount < MAX_REFRESH_RETRIES) {
              refreshRetryCount++;
              console.log(
                `[SliderSolver] 等待滑块就绪时检测到"刷新/连接中断"弹窗，将刷新页面重试 (${refreshRetryCount}/${MAX_REFRESH_RETRIES})`
              );
              needReloadForRefresh = true;
              attempt--;
              sliderInfo = null;  // 跳出等待循环
              break;
            }
          }
        }
        if (!sliderInfo && needReloadForRefresh) continue;
      }

      if (!sliderInfo) {
        lastError = `检测到滑块容器 ${detected.selector}，但滑块按钮未加载（可能仍在转圈）`;
        console.warn(`[SliderSolver] ${lastError}`);
        // 检查是否需要点击重试（可能上一轮失败后处于错误状态）
        const retryCheck = await checkClickToRetry(page, frame);
        if (retryCheck.needsClick && clickRetryCount < MAX_CLICK_RETRIES) {
          clickRetryCount++;
          console.log(`[SliderSolver] 检测到重试提示，点击弹窗触发新滑块 (${clickRetryCount}/${MAX_CLICK_RETRIES})`);
          await clickRetryToResetSlider(page, frame, retryCheck.retryTarget);
          await page.waitForTimeout(2500);
        }
        continue;
      }

      const { button, trackWidth, buttonBox, ownerFrame } = sliderInfo;
      const startX = buttonBox.x + buttonBox.width / 2;
      const startY = buttonBox.y + buttonBox.height / 2;

      console.log(`[SliderSolver] 开始拖动滑块: startX=${startX}, distance=${trackWidth}, attempt=${attempt}`);
      // 拖动前截图（视觉复盘）
      await saveDebugScreenshot(page, `slider-pre-${attempt}`);
      // === 拖动前真人行为模拟（与商业版对齐） ===
      // 问题：从日志看，拖动后立即出现 .errloading，FireyeJS 在拖动过程中就判定为机器人。
      // 原因：FireyeJS 不仅分析拖动轨迹，还分析拖动前的鼠标行为。
      //       机器人特征：页面加载后鼠标完全静止，直到拖动时才突然移动。
      //       真人特征：看到滑块后鼠标会在页面上移动、犹豫一下再拖动。
      // 优化：拖动前模拟真人浏览行为（鼠标移动 2-3 次 + 移到滑块附近 + 阅读停顿 0.8-2s）。
      try {
        // 1. 在页面上随机移动鼠标 2-3 次（模拟真人看到滑块后鼠标的随机移动）
        const viewportWidth = page.viewportSize()?.width || 1280;
        const viewportHeight = page.viewportSize()?.height || 720;
        for (let m = 0; m < 2 + Math.floor(Math.random() * 2); m++) {
          const rx = 100 + Math.random() * (viewportWidth - 200);
          const ry = 100 + Math.random() * (viewportHeight - 200);
          await page.mouse.move(rx, ry, { steps: 3 + Math.floor(Math.random() * 5) });
          await page.waitForTimeout(200 + Math.random() * 400);
        }
        // 2. 鼠标移动到滑块附近（但不是直接到滑块中心）
        await page.mouse.move(startX + (Math.random() - 0.5) * 60, startY + (Math.random() - 0.5) * 40, { steps: 5 });
        await page.waitForTimeout(300 + Math.random() * 500);
        // 3. 阅读弹窗的停顿（真人看到滑块后会停留 1-2 秒阅读弹窗内容）
        await page.waitForTimeout(800 + Math.random() * 1200);
      } catch (e) {
        // 行为模拟失败不影响拖动流程
        console.log(`[SliderSolver] 拖动前行为模拟失败（可忽略）: ${safeErrorType(e)}`);
      }
      try {
        // 使用 page.mouse API 生成真实鼠标事件（isTrusted=true），对抗 Baxia 风控的合成事件检测
        // 传入 attempt 让每次重试使用不同的滑动速度和停顿策略，模拟真人滑动
        // 三种轨迹方案轮换（与商业版默认轨迹方案对齐，强化本地求解能力）：
        //   attempt % 3 === 1 → humanLikeDrag（容器内拖动，多策略速度）
        //   attempt % 3 === 2 → humanLikeDragOutOfContainer（超出容器拖动，Y 大幅偏移）
        //   attempt % 3 === 0 → humanPhysicsDrag（最小急动度剖面，物理模型）
        const dragStrategy = attempt % 3;
        if (dragStrategy === 1) {
          console.log(`[SliderSolver] attempt=${attempt} 使用容器内拖动方法 (humanLikeDrag)`);
          await humanLikeDrag(page, ownerFrame || frame, button, startX, startY, trackWidth, attempt);
        } else if (dragStrategy === 2) {
          console.log(`[SliderSolver] attempt=${attempt} 使用超出容器拖动方法 (humanLikeDragOutOfContainer)`);
          await humanLikeDragOutOfContainer(page, ownerFrame || frame, button, startX, startY, trackWidth, attempt);
        } else {
          console.log(`[SliderSolver] attempt=${attempt} 使用最小急动度物理拖动方法 (humanPhysicsDrag)`);
          await humanPhysicsDrag(page, ownerFrame || frame, button, startX, startY, trackWidth, attempt);
        }
      } catch (e: any) {
        lastError = '拖动滑块异常，请稍后重试';
        console.error(`[SliderSolver] operation=drag errorType=${safeErrorType(e)}`);
        // 拖动异常同样重置会话，避免惩罚态残留
        needReloadForRefresh = true;
        needCooldownAfterHumanAction = true;
        continue;
      }

      // 等待验证结果
      await page.waitForTimeout(2000 + Math.random() * 1200);
      // 拖动后截图
      const postShot = await saveDebugScreenshot(page, `slider-post-${attempt}`);
      if (postShot) screenshotPath = postShot;

      // 场景5/场景6：拖动后也检测"刷新/连接中断"弹窗（可能在滑动过程中弹出）
      if (await checkRefreshDialog(page)) {
        if (refreshRetryCount < MAX_REFRESH_RETRIES) {
          refreshRetryCount++;
          console.log(
            `[SliderSolver] 拖动后检测到"刷新/连接中断"弹窗，将刷新页面重试 (${refreshRetryCount}/${MAX_REFRESH_RETRIES})`
          );
          needReloadForRefresh = true;
          attempt--;
          continue;
        } else {
          console.warn(`[SliderSolver] 刷新弹窗重试已达上限 (${MAX_REFRESH_RETRIES})`);
        }
      }

      const solved = await checkSolved(page, ownerFrame || frame);

      if (solved) {
        // 场景4：滑块通过后检测"下载消息失败"，需刷新页面后重新滑动
        const downloadFailed = await checkDownloadMessageFailed(page);
        if (downloadFailed && downloadRetryCount < MAX_DOWNLOAD_RETRIES) {
          downloadRetryCount++;
          console.log(
            `[SliderSolver] 滑块通过但检测到"下载消息失败"，将在下一轮刷新页面重试 (${downloadRetryCount}/${MAX_DOWNLOAD_RETRIES})`
          );
          needReloadForDownload = true;
          // 刷新重试不消耗滑动配额，回退 attempt 计数
          attempt--;
          continue;
        }

        console.log('[SliderSolver] 滑块验证通过！');
        {
          const cookieStr = await exportContextCookies(context);
          return {
            ok: true,
            solved: true,
            captchaDetected: true,
            attempts,
            durationMs: Date.now() - startTime,
            cookieStr,
          };
        }
      }

      // 场景2：滑动失败后检测"验证失败，点击框体重试"
      const retryCheck = await checkClickToRetry(page, ownerFrame || frame);
      if (retryCheck.needsClick && clickRetryCount < MAX_CLICK_RETRIES) {
        clickRetryCount++;
        console.log(`[SliderSolver] 检测到失败提示，点击弹窗触发新滑块 (${clickRetryCount}/${MAX_CLICK_RETRIES})`);
        await clickRetryToResetSlider(page, ownerFrame || frame, retryCheck.retryTarget);
        // 等待新滑块加载（点击后弹窗会重新加载，可能伴随转圈动画，需足够等待）
        await page.waitForTimeout(2500);

        // 场景5：点击重试后检测"刷新页面"小弹窗（多次失败后会出现）
        if (await checkRefreshDialog(page)) {
          if (refreshRetryCount < MAX_REFRESH_RETRIES) {
            refreshRetryCount++;
            console.log(
              `[SliderSolver] 检测到"刷新页面"小弹窗，将刷新页面重试 (${refreshRetryCount}/${MAX_REFRESH_RETRIES})`
            );
          }
        }
      }

      // === 失败后策略：同页连续失败会累积 Baxia 惩罚态 ===
      // 关键：每次拖动失败后都彻底重置（关弹窗/清会话 + 从首页重开消息页），
      // 而不是只在同页点"框体重试"。视觉复盘显示同页连续拖动几乎全是 error:xxx。
      lastError = `第 ${attempt} 次拖动后未通过验证`;
      console.warn(`[SliderSolver] ${lastError}，将重置页面会话后重试`);

      if (attempt >= HUMAN_ACTION_THRESHOLD && humanActionCount < MAX_HUMAN_ACTIONS) {
        humanActionCount++;
        console.log(
          `[SliderSolver] 连续 ${attempt} 次失败，触发真人行动：关闭弹窗→刷新页面→冷静期 ` +
          `(${humanActionCount}/${MAX_HUMAN_ACTIONS})`
        );
        const closed = await closeCaptchaDialog(page);
        if (closed) {
          console.log(`[SliderSolver] 已关闭弹窗，等待页面变化...`);
          await page.waitForTimeout(1500);
        } else {
          console.log(`[SliderSolver] 未找到关闭按钮，直接刷新页面`);
        }
        needCooldownAfterHumanAction = true;
      } else {
        // 常规失败：尽量关弹窗后重置
        await closeCaptchaDialog(page).catch(() => false);
        await page.waitForTimeout(400 + Math.random() * 600);
      }

      // 清理可能残留的本地失败计数（部分 Baxia 状态写在 storage）
      try {
        await page.evaluate(() => {
          try { localStorage.clear(); } catch { /* ignore */ }
          try { sessionStorage.clear(); } catch { /* ignore */ }
        });
      } catch { /* ignore */ }

      needReloadForRefresh = true;
      await page.waitForTimeout(800 + Math.random() * 700);
    }

    // 所有重试都失败，截图保存
    try {
      if (!options.saveFailureScreenshot) throw new Error('failure screenshots are disabled');
      const screenshotDirectory = path.resolve(
        process.env.CRAWLER_SCREENSHOT_DIR || path.join(process.cwd(), 'crawler-screenshots')
      );
      await fs.mkdir(screenshotDirectory, { recursive: true, mode: 0o700 });
      screenshotPath = path.join(screenshotDirectory, `slide-fail-${Date.now()}.png`);
      await page.screenshot({ path: screenshotPath, fullPage: true });
      console.log('[SliderSolver] 失败截图已保存到受控目录');
    } catch {
      // ignore
    }

    return {
      ok: false,
      solved: false,
      captchaDetected: true,
      attempts,
      error: lastError || `滑块验证失败，已重试 ${maxRetries} 次`,
      screenshotPath,
      durationMs: Date.now() - startTime,
    };
  } catch (e: any) {
    console.error('[SliderSolver] 执行异常', {
      errorType: e instanceof Error ? e.name : 'UnknownError',
    });
    return {
      ok: false,
      solved: false,
      captchaDetected: false,
      attempts: 0,
      error: e?.message || String(e),
      durationMs: Date.now() - startTime,
    };
  } finally {
    options.signal?.removeEventListener('abort', abortBrowser);
    try {
      // launchPersistentContext 时优先关 context；否则关 browser
      if (context && typeof (context as any).close === 'function') {
        await (context as any).close().catch(() => {});
      } else if (browser) {
        await browser.close().catch(() => {});
      }
    } catch {
      // ignore
    }
    // 清理持久化上下文的 userDataDir，防止用户 Cookie/localStorage 残留磁盘
    if (userDataDirForCleanup) {
      try {
        await fs.rm(userDataDirForCleanup, { recursive: true, force: true });
      } catch {
        // 清理失败不影响主流程，下次启动会创建新目录
      }
    }
  }
}
