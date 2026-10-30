# Windows 绿色便携宿主与 GitHub 打包

本轮增加 Windows 桌面宿主、系统托盘、原生电源事件桥接和 EXE 打包作业。依赖下载只在 GitHub Windows runner 执行，本机不安装 Qt 或 PyInstaller。**当前没有本机生成或验收过的 EXE；以对应提交的 Actions 结果为准。**

普通程序仍未装配真实闲鱼账号、扫码登录和已验证的生产适配器，因此显示“未连接闲鱼 · 自动发货未启用”。绿色仅在实际运行时、全部账号已连接、无暂停原因且已完成核验时出现。不能把窗口、托盘或打包成功当作真实发货验收成功。

## 目录与运行环境

```text
XianyuPilot/
  XianyuPilot.exe
  _internal/                  # Python、Qt/Chromium、依赖、前端、SQL 迁移（只读）
  portable-manifest.json      # 源码提交、SHA256、待人工验收事项
  PORTABLE-README.txt
  data/                       # 首次运行创建，不随空白发布包分发
    app.sqlite3
    app.lock
    assets/
    profiles/console/         # 本地控制台浏览器资料
    profiles/system/          # 当前进程及子进程的 AppData 后备路径
    logs/desktop.log
    logs/chromium/
    tmp/
    backups/
```

交付形式为目录式 EXE ZIP，用户解压完整目录并双击，无需安装 Python、Node.js、Git、系统浏览器或 WebView2。不要单独复制 EXE。目标为 Windows 10/11 x64；其他版本不在当前验收范围内。

冻结状态的唯一业务根为 `Path(sys.executable).resolve().parent / 'data'`，源码状态使用仓库根，不读取 cwd 定位业务文件。`sys._MEIPASS` 仅用于读取内嵌静态资源，不能成为数据根。启动先验证全部数据子目录可写并拒绝链接/联接，再加载 GUI 或服务；失败弹窗提示移到具备写权限的普通文件夹，不回退用户目录。

Python 临时文件、SQLite/WAL、日志、浏览器资料、下载临时区和 QML 缓存都锚定 data。当前进程及子进程的 APPDATA/LOCALAPPDATA/XDG 路径重定向到 data；覆盖外部 Chromium flags，移除继承的远程调试/禁用沙箱环境。Qt ProfileBuilder 在创建资料对象前设置明确路径，HTTP 缓存默认放内存。

控制台仅访问自己的 127.0.0.1 端口，拒绝外部页面、新窗口、文件选择、下载和权限申请，不打开系统浏览器。正式扫码账号资料尚未接入，后续须新增独立的 data/profiles 受控资料区及账号归属核验。跨电脑后平台或系统凭据可能要求重新登录，不承诺未实现的登录迁移。

## 托盘与退出

- 点击 X 隐藏主窗口，进程和本机服务继续运行；首次隐藏有气泡提示。已连接且运行中才提示自动发货持续，离线或暂停时显示实际状态。
- 双击图标切换显示/隐藏；菜单提供打开、隐藏、暂停/恢复、安全退出。状态区分未连接、已暂停、重新对账、需核查和运行中。
- 宿主在加载 Chromium 前获取 data/app.lock；后端借用已持有的同一把锁。重复双击会报错，不建立第二个浏览器资料或发货执行器。
- 暂停立即关闭跨线程发送闸门。manual、network、suspend、shutdown 各自保留，网络恢复不会清除手动暂停。恢复先登记账号对账要求，再放行对应原因；未知任务仍锁定。
- Windows `WM_POWERBROADCAST` 休眠事件暂停，唤醒事件要求恢复核验。网络探针读取 Windows 连接状态，不访问第三方测试站点；它只说明系统连接可用，真正可发货还需官方查询和 IM 状态通过。
- 退出先关闭新发送、断开 IM，等待账号执行器和短事务结束，将未确认在途操作保留为未知，再结束服务、释放数据库和 Chromium 资料，最后释放实例锁。不用强杀假装优雅退出。Windows 强制关机仍可能终止进程，重启的未知锁定继续生效。

## GitHub 构建流程

沿用 `.github/workflows/ci.yml` 的分支/PR/手动触发，在复用工作流中新增 `windows-portable`，没有新增发布写权限。

1. 等待原后端回归、Web 构建和工作流检查通过。
2. 下载同一 Actions 运行中已测试的 web-dist，验证提交和文件哈希。
3. 在 GitHub Windows x64 runner 安装原 API 哈希锁依赖、固定版本 GUI/打包依赖及测试工具，保留实际安装版本清单。
4. 无物理桌面测试 Qt 关闭驻留、暂停/恢复及电源桥接契约；模拟 shell 可用性，不能证明真实托盘验收。
5. PyInstaller 使用 `exclude_binaries=True` 和 COLLECT 构建 onedir，携带 Python DLL、QtWebEngineProcess、Chromium 资源、VC runtime、Qt 许可、原前端及 SQL 迁移。禁止 onefile 自解压。
6. 在一次性副本中从外部 cwd 执行 `--self-check`；移动到中文空格路径后复查相同数据库标识，再用 `--webengine-check` 验证内置 Chromium 渲染 Vue。测试 PATH 仅保留 Windows System32，移除 PYTHONHOME/PYTHONPATH，不借用 runner 的 Python/Node/Git。
7. 将继承的用户/Temp 路径指向空监测目录，检查没有使用后备目录。这不能替代系统级全程 I/O 跟踪。
8. 对空白构建记录 SHA256，验证必需运行组件、无 data/凭据/真实配置；生成 ZIP 后重新核对每个文件。

成功后在 Actions → 对应运行 → Artifacts 下载：

- `xianyu-pilot-windows-portable`：绿色 EXE ZIP 和 SHA256SUMS.txt。
- `windows-portable-validation`：Qt 契约、启动/移动/渲染报告、依赖清单及 PyInstaller 警告。

便携构建加入 build-gate，失败/跳过阻断质量门禁。现有公开 Release 仍只发布原源码包，便携包先作为 Actions 预览产物。单纯本地提交不会启动远端 Actions。

## 分阶段验收记录

| 条目 | 本轮证据/边界 |
| --- | --- |
| 10 单并发无超卖、未知不回库 | 本地真实 SQLite Mock 测试 |
| 问候 + 3 条卡密 + 1 份说明/图片/链接 | 本地顺序与数量测试 |
| 手动/休眠/断网暂停原因独立 | 本地闸门与恢复测试 |
| 退出等待在途提交 | 本地执行器及真实本机服务线程测试 |
| Qt 窗口/托盘和电源桥接 | 本地语法检查；实际 Qt 测试在 GitHub 安装后执行 |
| 冻结 EXE、移除开发 PATH、移动、内嵌渲染 | 已编写 GitHub 门禁，尚无本轮远端结果 |
| 干净虚拟机解压即用 | 待 VM 验收，不能用 runner 代替 |
| 真实换盘符、U 盘和登录状态 | 待实际验证；同盘换目录只覆盖一部分 |
| C:/Users/AppData 全程零业务写入 | 待主进程及 QtWebEngineProcess 完整 I/O 跟踪 |
| 关闭窗口后真实接单、0.01 元订单履约 | 待账号及官方契约联调，不能用 Mock 代替 |
| 物理断电、原生托盘驻留 | 待真实 Windows 验收 |

“3 秒”为正常 WebSocket 环境下的真实订单验收目标，15–30 秒轮询兜底无法同时保证 3 秒触发。网络超时仍须暂停，不能为速度跳过核验。

设计依据：[Qt 资料构建器](https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineCore/QWebEngineProfileBuilder.html)、[PyInstaller 目录式部署](https://pyinstaller.org/en/stable/operating-mode.html)、[Windows 电源广播](https://learn.microsoft.com/en-us/windows/win32/power/wm-powerbroadcast)。文档说明机制，不代替本程序验收。
