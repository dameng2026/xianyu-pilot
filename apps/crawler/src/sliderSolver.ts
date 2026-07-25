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
// 本脚本覆盖：webdriver / chrome / plugins / languages / permissions /
// WebGL vendor&renderer / hardwareConcurrency / deviceMemory / Canvas 微扰动
const ANTI_DETECT_SCRIPT = `
(() => {
  try {
    // 1. 屏蔽 navigator.webdriver（删除原型属性，降低 'webdriver' in navigator 命中率）
    try { delete Object.getPrototypeOf(navigator).webdriver; } catch (e) {}
    Object.defineProperty(navigator, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
    try {
      Object.defineProperty(Navigator.prototype, 'webdriver', {
        get: () => undefined,
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
  let stepsBase: number;
  let stepDelayMin: number;
  let stepDelayMax: number;
  // 中间停顿点位置（progress 0.3-0.7 之间随机）
  const pausePoint = 0.3 + Math.random() * 0.4;
  const pauseDurationMs = 300;  // 停顿0.3秒
  let pausePoints: number[] = [];

  switch (attempt) {
    case 1:
      // 标准速度：30-40步，20-50ms间隔，总时长约 0.8-1.8秒
      stepsBase = 30;
      stepDelayMin = 20;
      stepDelayMax = 50;
      break;
    case 2:
      // 中速：35-45步，30-70ms间隔，无停顿，总时长约 1.2-2.5秒
      stepsBase = 35;
      stepDelayMin = 30;
      stepDelayMax = 70;
      break;
    case 3:
      // 较快：25-35步，15-40ms间隔，无停顿，总时长约 0.5-1.2秒
      stepsBase = 25;
      stepDelayMin = 15;
      stepDelayMax = 40;
      break;
    case 4:
      // 慢速：40-50步，40-90ms间隔 + 中间停顿0.3秒，总时长约 2-4秒
      stepsBase = 40;
      stepDelayMin = 40;
      stepDelayMax = 90;
      pausePoints = [pausePoint];
      break;
    default:
      // attempt >= 5: 随机策略组合（不设停顿，避免太慢）
      stepsBase = 30 + Math.floor(Math.random() * 15);
      stepDelayMin = 20 + Math.floor(Math.random() * 30);
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
  await page.mouse.down();
  // 按下后短暂停顿（80-180ms，模拟按下后开始滑动）
  await page.waitForTimeout(80 + Math.random() * 100);
  // 按下后微小漂移（真人按下到开始拖动之间鼠标常有 1-2px 漂移，非完美静止）
  await page.mouse.move(actualStartX + (Math.random() - 0.5) * 3, actualStartY + (Math.random() - 0.5) * 3, { steps: 3 });
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

    // 使用 page.mouse.move 生成真实 mousemove 事件
    // steps: 3 让每步移动有 3 个 mousemove 事件，肉眼可见鼠标在"滑"而非瞬移
    // 同时保持每个 mousemove 事件之间的距离合理（约 3-5px），符合 Baxia 对轨迹密度的要求
    await page.mouse.move(targetX, currentY, { steps: 3 });

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
  await page.mouse.move(actualStartX + distance + overshoot, actualStartY + (Math.random() - 0.5) * 10, { steps: 4 });
  await page.waitForTimeout(50 + Math.random() * 80);
  await page.mouse.move(actualStartX + distance, actualStartY + (Math.random() - 0.5) * 6, { steps: 4 });

  // 5. 释放前微调（真人释放前常有 1-2 次微小位置修正，非完美静止释放）
  const numAdjustments = Math.random() < 0.7 ? 1 : 2;  // 70% 概率 1 次，30% 概率 2 次
  for (let a = 0; a < numAdjustments; a++) {
    await page.waitForTimeout(40 + Math.random() * 60);
    const adjustX = (Math.random() - 0.5) * 4;
    const adjustY = (Math.random() - 0.5) * 4;
    await page.mouse.move(actualStartX + distance + adjustX, actualStartY + adjustY, { steps: 2 });
  }
  // 释放前短暂停顿（50-120ms）
  await page.waitForTimeout(50 + Math.random() * 70);
  // 鼠标释放（真实 mouseup 事件）
  await page.mouse.up();
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

  // 2. 鼠标按下
  await page.mouse.down();
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

    await page.mouse.move(targetX, currentY, { steps: 1 });

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
  await page.mouse.move(startX + distance + overshoot, startY + overshootYOffset, { steps: 2 });
  await page.waitForTimeout(50 + Math.random() * 80);
  // 回到终点（Y 仍有偏移，可能在容器外）
  const endYOffset = (Math.random() - 0.5) * 30;  // ±15px
  await page.mouse.move(startX + distance, startY + endYOffset, { steps: 2 });

  // 6. 释放前停顿
  await page.waitForTimeout(50 + Math.random() * 70);
  await page.mouse.up();
}

/**
 * 检测滑块验证是否通过
 */
async function checkSolved(page: Page, frame: any): Promise<boolean> {
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

  // 滑块弹窗消失也视为通过
  const stillHasCaptcha = await detectCaptcha(page);
  return !stillHasCaptcha.detected;
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
  const retrySelectors = [
    '.nc_error',
    '.errloading',
    '#nc_1_refresh1',
    '.nc-lang-cnt',
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
          return { needsClick: true, retryTarget: elem };
        }
      } catch { /* ignore */ }
    }
  }

  // 2. 检测"验证失败，点击框体重试"文本
  for (const f of searchFrames) {
    if (!f) continue;
    try {
      const hasRetryText = await f.evaluate(() => {
        const text = document.body ? document.body.innerText : '';
        // 覆盖多种失败提示文本
        return /验证失败|点击框体重试|点击重试|请重试|error:|滑块加载失败|加载失败|滑动失败|验证未通过/i.test(text);
      }).catch(() => false);
      if (hasRetryText) {
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
 */
async function checkRefreshDialog(page: Page): Promise<boolean> {
  const checkFrame = async (f: any) => {
    try {
      return await f.evaluate(() => {
        const text = document.body ? document.body.innerText : '';
        // 匹配刷新弹窗的文本特征
        if (/请刷新页面|刷新重试|刷新页面后重试|网络异常.*刷新|页面已失效.*刷新|重新加载|刷新试试|刷新后重试|连接中断|连接已断开|网络连接中断|请重连|重新连接/i.test(text)) {
          return true;
        }
        // 检测常见的刷新/重连按钮/弹窗元素
        const refreshSelectors = [
          '.refresh-btn', '.reload-btn', '.refresh-dialog',
          '.modal-refresh', '.dialog-refresh',
          'button[class*="refresh"]', 'button[class*="reload"]',
          'button[class*="reconnect"]', '.reconnect-btn',
          '.ant-modal-confirm', '.next-dialog',
          '.next-dialog-message', '.ant-modal-body',
        ];
        for (const sel of refreshSelectors) {
          const elem = document.querySelector(sel);
          if (elem) {
            const elemText = elem.textContent || '';
            if (/刷新|重新加载|reload|refresh|连接中断|重连|重新连接/i.test(elemText)) {
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

  if (await checkFrame(page)) return true;
  for (const f of page.frames()) {
    if (f === page.mainFrame()) continue;
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
  // 默认重试 4 次（开源版已削弱本地求解能力）
  const retries = Number(options.maxRetries ?? 4);
  const maxRetries = Number.isSafeInteger(retries) ? Math.max(1, Math.min(retries, 10)) : 4;
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
    const HUMAN_ACTION_THRESHOLD = 3;  // 连续失败 3 次后触发
    const MAX_HUMAN_ACTIONS = 1;       // 开源版已削弱真人行动模拟次数（原 2 次，削弱为 1 次）
    let humanActionCount = 0;
    // 真人行动刷新后的冷静期标志：刷新完成后不立即操作，等待 3-7 秒让 Baxia 检测状态自然重置
    let needCooldownAfterHumanAction = false;

    // 使用 while 循环，让场景4(下载消息失败)和场景5(刷新小弹窗)的刷新重试
    // 不占用 maxRetries 的滑动重试配额，否则5次滑动配额会被刷新操作耗尽
    let attempt = 0;
    while (attempt < maxRetries) {
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

      // 真人行动冷静期：刷新完成后不立即操作，等待 3-7 秒让 Baxia 检测状态自然重置
      if (needCooldownAfterHumanAction) {
        needCooldownAfterHumanAction = false;
        const cooldownMs = 3000 + Math.floor(Math.random() * 4000);  // 3-7 秒
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
      // 阅读弹窗的短暂停顿
      await page.waitForTimeout(400 + Math.random() * 700);
      try {
        // 使用 page.mouse API 生成真实鼠标事件（isTrusted=true），对抗 Baxia 风控的合成事件检测
        // 传入 attempt 让每次重试使用不同的滑动速度和停顿策略，模拟真人滑动
        // 开源版仅使用容器内拖动方法（已削弱本地求解能力，移除超出容器拖动方法轮换）
        console.log(`[SliderSolver] attempt=${attempt} 使用容器内拖动方法`);
        await humanLikeDrag(page, ownerFrame || frame, button, startX, startY, trackWidth, attempt);
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
