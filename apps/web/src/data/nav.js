export const navGroups = [
  {
    title: '概览',
    items: [
      { key: 'dashboard', label: '导航面板', icon: 'dashboard' },
      { key: 'data', label: '数据面板', icon: 'data' },
    ],
  },
  {
    title: '账号与商品',
    items: [
      { key: 'accounts', label: '闲鱼账号', icon: 'account' },
      { key: 'connections', label: '连接管理', icon: 'link' },
      { key: 'products', label: '商品管理', icon: 'product' },
      { key: 'orders', label: '订单管理', icon: 'record' },
      { key: 'product-publish', label: '发布商品', icon: 'publish', child: true },
    ],
  },
  {
    title: '消息',
    items: [
      { key: 'messages', label: '在线消息', icon: 'chat' },
    ],
  },
  {
    title: '自动化',
    items: [
      { key: 'auto-delivery', label: '自动发货', icon: 'truck' },
      { key: 'delivery-source-library', label: '货源库', icon: 'board', child: true },
      { key: 'delivery-statement', label: '发货声明', icon: 'board', child: true },
      { key: 'card-warehouse', label: '卡密仓库', icon: 'key', child: true },
      { key: 'delivery-records', label: '发货记录', icon: 'record' },
      { key: 'scheduled-tasks', label: '定时任务', icon: 'clock' },
      { key: 'auto-reply', label: '自动回复', icon: 'reply' },
      { key: 'slider-solve-records', label: '滑块求解', icon: 'shield' },
      { key: 'remote-slider-api', label: 'API 对接', icon: 'shield', child: true },
    ],
  },
  {
    title: '系统',
    items: [
      { key: 'logs', label: '操作日志', icon: 'log' },
      { key: 'feedback', label: '反馈建议', icon: 'reply' },
      { key: 'ad-application', label: '广告申请', icon: 'opportunity' },
      { key: 'settings-notify', label: '通知设置', icon: 'bell' },
      { key: 'settings-system', label: '系统设置', icon: 'settings' },
    ],
  },
]

export const settingsTabs = [
  { key: 'settings-ai-cs', label: 'AI 客服配置', icon: 'message' },
  { key: 'settings-system', label: '系统配置', icon: 'settings' },
  { key: 'settings-model', label: '模型配置', icon: 'ai' },
  { key: 'settings-embedding', label: '向量模型', icon: 'ai' },
  { key: 'settings-rag', label: 'RAG 知识库', icon: 'message' },
  { key: 'settings-product', label: '商品操作', icon: 'product' },
  { key: 'settings-about', label: '关于我们', icon: 'help' },
]

export const pageTitles = {
  'not-found': ['页面不存在', '该链接可能已失效、地址有误或对应功能已下线'],
  dashboard: ['导航面板', '系统导航中心，帮助你快速进入常用功能'],
  data: ['数据面板', '按需查看运营数据、发货情况与业务趋势'],
  accounts: ['闲鱼账号', '管理账号状态、登录情况与连接健康度'],
  connections: ['连接管理', '统一查看账号连接、WebSocket 与 Cookie 状态'],
  products: ['商品管理', '管理商品信息、同步状态、自动发货与自动回复配置'],
  orders: ['订单管理', '集中查看订单状态、买家信息、发货情况与异常提醒'],
  'product-publish': ['发布商品', '创建并发布闲鱼商品'],
  messages: ['在线消息', '集中处理买家咨询与消息会话'],
  'message-center': ['在线消息', '集中处理买家咨询与消息会话'],
  'auto-delivery': ['自动发货', '按商品配置自动发货规则、时机与发送方式'],
  'delivery-source-library': ['货源库', '统一管理文本货源，支持 AI 推荐适配商品并批量配置'],
  'delivery-statement': ['发货声明', '管理发货声明文案与生效范围'],
  'card-warehouse': ['卡密仓库', '管理卡密库存、分组与使用记录'],
  'delivery-records': ['发货记录', '追踪自动发货、异常与补发情况'],
  'scheduled-tasks': ['定时任务', '查看和维护定时任务执行计划'],
  'auto-reply': ['自动回复', '配置 AI 自动回复策略、作用域与兜底话术'],
  'slider-solve-records': ['滑块求解', '查看滑块验证求解记录与状态'],
  'remote-slider-api': ['API 对接', '对接商业版远程滑块求解 API，提升求解通过率'],
  logs: ['操作日志', '查看系统操作与关键行为记录'],
  feedback: ['反馈建议', '提交产品建议、Bug 反馈与功能诉求'],
  'ad-application': ['广告申请', '查看真实商业套餐；未配置时提交与支付保持禁用'],
  'settings-ai-cs': ['系统设置 / AI 客服配置', '管理 AI 客服的人设、知识库、聊天规则与测试回复'],
  'settings-system': ['系统设置 / 系统配置', '查看开源版系统总览与各模块配置状态'],
  'settings-model': ['系统设置 / 模型配置', '集中管理通用模型与向量模型配置'],
  'settings-embedding': ['系统设置 / 向量模型配置', '单独维护向量模型（Embedding）用于 RAG 检索与语义召回'],
  'settings-rag': ['系统设置 / RAG 知识库', '单独管理知识库、文档上传与检索测试'],
  'settings-product': ['系统设置 / 商品操作', '管理商品相关系统级配置'],
  'settings-notify': ['系统设置 / 通知设置', '管理通知渠道、防重复发送与测试发送'],
  'settings-about': ['系统设置 / 关于我们', '查看项目版本、更新日志与站点说明'],
  profile: ['个人中心', '管理管理员资料、安全设置与部署概况'],
}
