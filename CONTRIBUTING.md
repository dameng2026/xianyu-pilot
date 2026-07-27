# 贡献指南

感谢你对闲鱼助手开源项目的关注！本文档描述了参与贡献的流程和规范。

## 行为准则

- 保持尊重和友善的沟通态度
- 对不同观点保持开放心态
- 关注项目整体利益，而非个人偏好

## 开发环境准备

### 前置要求

- Python 3.11+
- Node.js 22+（推荐使用 LTS 版本）
- MySQL 8.0
- Redis 7
- Git

### 本地启动

1. 克隆仓库并安装依赖：

```bash
git clone https://github.com/dameng2026/XianYuPilo.git
cd XianYuPilo
cp .env.example .env
# 编辑 .env，填写数据库密码等必填项
```

2. 后端依赖安装：

```bash
cd apps/api
pip install -r requirements.txt
```

3. 前端依赖安装：

```bash
cd apps/web
npm install
```

4. 启动服务：

```bash
# Windows
start.bat

# Linux/Mac
./start.sh
```

## 代码规范

### Python 后端

- 使用 `async/await`，不使用回调风格
- 中间件文件放在 `apps/api/app/middleware/`，一个文件只处理一个关注点
- 路由文件不参与中间件重构
- 数据库查询使用 SQLAlchemy 2.0 async 风格
- 新增配置项从 `xianyu_sys_setting` 表读取，使用 60 秒缓存

### Vue 前端

- 使用 Vue 3 Composition API（`<script setup>`）
- 请求统一通过 `apps/web/src/utils/request.js` 发送
- 页面组件放在 `apps/web/src/pages/`，复用组件放在 `apps/web/src/components/`
- 生产代码中禁止裸露的 `console.log`（如需调试，包裹在 `if (import.meta.env.DEV)` 中）

### 通用

- 提交前确保 `npm run build` 和 Python 语法检查通过
- 新增功能需附带基础测试
- 不引入未授权的第三方资源（图片、字体、数据）

## 提交规范

### Git 提交信息

使用约定式提交（Conventional Commits）格式：

```
<type>(<scope>): <description>

<body>
```

类型（type）：
- `feat`：新功能
- `fix`：Bug 修复
- `docs`：文档变更
- `style`：代码格式调整（不影响功能）
- `refactor`：重构（既不是新功能也不是修 Bug）
- `perf`：性能优化
- `test`：测试相关
- `chore`：构建、依赖、配置等杂项

示例：
```
feat(auth): 添加邮箱验证码登录支持
fix(middleware): 修复公开路径白名单缺失问题
docs(readme): 更新部署说明
```

### 分支策略

- `main`：稳定分支，保持可发布状态
- 功能开发：`feat/<feature-name>`
- Bug 修复：`fix/<bug-description>`

## Pull Request 流程

1. 从 `main` 创建功能分支
2. 开发并测试通过后提交 PR
3. PR 描述需包含：变更说明、测试方式、影响范围
4. 等待 Code Review，根据反馈调整
5. 合并后删除功能分支

## 安全问题

如发现安全漏洞，请勿直接提交 Issue 或 PR。参阅 [`SECURITY.md`](SECURITY.md) 中的披露流程，私下联系维护者。

## 许可证

许可证由项目权利人选择后通过 `LICENSE` 文件正式声明。在许可证确定前，请遵循仓库根目录 `LICENSE` 文件（如已存在）的条款。
