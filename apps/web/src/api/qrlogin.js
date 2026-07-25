import request from '../utils/request.js'

export const generateQrLogin = (data = {}) => request.post('/qrlogin/generate', data)
// 扫码状态轮询标记为 background，避免闲鱼 API 响应慢导致请求堆积时
// 触发全局"正在同步数据..."横幅遮挡扫码模态框。
export const getQrLoginStatus = (sessionId, data = {}) => request.post(
  `/qrlogin/status/${encodeURIComponent(sessionId)}`,
  data,
  { background: true },
)
export const cleanupQrLogin = () => request.post('/qrlogin/cleanup', {})
