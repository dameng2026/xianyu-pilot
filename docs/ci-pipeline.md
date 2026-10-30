# CI 检查、构建与本机发布

更新日期：2026-10-29（北京时间）。

本次参考 [12-test-12/ai-goofish-monitor 的流水线](https://github.com/12-test-12/ai-goofish-monitor/blob/master/.github/workflows/docker-image.yml)，核对文件 blob：`dfba3bd433cc85888fe15fa2920ba3738d2f1484`。采用其独立检查、安全审计、测试产物复用、源码与文件校验、统一质量门禁和下载摘要等做法。

按本项目已确认要求，所有检查、构建和发布均直接在宿主运行，不使用 Docker、容器作业或容器服务。原源码部署包继续保留；新增 Windows x64 目录式 EXE 预览构建，依赖在 GitHub 下载，范围见 [Windows 便携说明](windows-portable.md)。尚未取得本轮远端构建结果。

## 工作流组织

| 文件 | 职责 |
| --- | --- |
| `.github/workflows/ci.yml` | 触发、调用构建和审计、总质量门禁、受限 Release 发布 |
| `.github/workflows/ci-checks.yml` | 可复用的语法校验、CI 回归、Linux/Windows 后端回归、Web 测试、Web/Crawler 构建、本机部署打包和校验 |
| `.github/workflows/security-audits.yml` | 可复用的 npm/pip 依赖审计、CodeQL、完整 Git 历史 Gitleaks 和安全门禁 |

旧 `docker-publish.yml` 已移除，避免重复触发旧发布逻辑。仓库历史 Docker 部署文件及镜像规则测试保留，但本流水线不调用它们构建或发布镜像。

## 触发与发布范围

| 事件 | 检查与构建 | 公开 Release |
| --- | --- | --- |
| 推送 `codex/**` 开发分支 | 执行 | 禁止 |
| 推送 `main` | 执行 | 不自动发布 |
| 面向 `main` 的 PR（含 Fork） | 执行 | 禁止 |
| 推送 `v*` 标签 | 执行 | 门禁通过且标签符合版本格式后发布 |
| 手动运行 | 执行 | 默认关闭；只有 main/版本标签且显式开启才允许 |

正式版本格式为 `v主版本.次版本.修订版本`，允许 `-rc.1` 等预发布后缀。手动 main 发布使用 `native-运行编号-尝试编号`，标记为预发布，避免更新正式最新版。版本标签在发布前再次核对其提交，已移动的标签拒绝发布。重跑不会覆盖既有 Release 或附件。

`contents: write` 仅授予最后的 Release 作业；普通检查只读源码。CodeQL 单独获得所需的 `security-events: write`，Fork PR 不上传扫描结果。没有容器仓库写权限，也不需要镜像仓库凭据。

同一 PR 或开发分支的新提交会取消旧检查以节省资源；主分支和版本发布不主动取消正在运行的任务。

## 构建产物如何复用

1. 使用现有锁文件安装 Python/Node 依赖，后端在 Linux 和 Windows 直接回归。
2. Web 运行现有 Node 单元测试，再构建 Web；Crawler 直接编译 TypeScript。
3. `scripts/ci/artifacts.py` 为两个 `dist` 记录源码提交、Actions 运行编号与全部文件的 SHA256。
4. 打包作业只下载本次运行的编译产物，核对归属和每个文件，拒绝缺失、额外、变更和链接文件。
5. `scripts/ci/native_package.py` 从当前提交读取原系统源码，与已验证的 `dist` 组合成 ZIP。只使用允许的路径，不打包真实配置、业务数据或独立桌面演示。
6. ZIP 内保存文件清单与校验和，外部附带 ZIP 的 `SHA256SUMS.txt`。发布前重新校验整个 ZIP 和内部文件。

重跑失败作业时使用同一个运行编号，允许继续使用本次运行中未重跑的成功构建；不同运行的产物不能混用。

## 下载与运行

检查成功后，可在该 Actions 运行的 Artifacts 中下载 `xianyu-pilot-native-source`；打包作业摘要也提供下载链接。合规版本发布后，GitHub Releases 提供 ZIP 与校验文件。

Windows 作业成功后另提供 `xianyu-pilot-windows-portable`（EXE ZIP/校验和）和 `windows-portable-validation`（构建与自检证据）。该作业加入构建门禁；便携预览包本轮不自动发布到公开 Release。

运行沿用原 `start-local.bat` / `start-local.sh`。需要本机 Python、Node.js、MySQL 和 Redis，首次依赖与浏览器安装需要联网。详细范围见 [原系统本机部署包](native-deployment.md)。

## 门禁与证据

构建组和审计组分别使用 `if: always()` 汇总完整作业结果，总 `Quality gate` 再检查两个组。失败、跳过、取消或缺少结果均阻断发布，不使用 `continue-on-error` 绕过审计。

保留现有高危阈值、CodeQL 对协议签名的精确源码审查例外和 Gitleaks 已撤销凭据的精确历史指纹例外。测试报告、审计 JSON 与 SARIF 保留 14 天，本机部署包保留 30 天。

本地可运行：

```powershell
python -m unittest discover -s tests/ci -v
python -m compileall -q scripts/ci
```

另需 actionlint、现有后端回归、Web 测试和两项前端构建。完整审计结果和远端运行情况以具体 Actions 记录为准；离线校验不代表真实数据库或账号联调已通过。
