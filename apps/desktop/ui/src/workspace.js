import { inject } from 'vue'

export async function api(path, payload) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...(payload === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Demo-CSRF': document.querySelector('meta[name="desktop-csrf"]').content },
      body: JSON.stringify(payload),
    }), signal: AbortSignal.timeout(25000),
  })
  const data = await response.json()
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : (data.detail || []).map(item => item.msg).join('；')
    throw new Error(detail || '操作失败，请核对输入')
  }
  return data
}
export function useWorkspace() { return inject('workspace') }
export function formatTime(value) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
export function parseParameters(text) {
  const result = {}
  for (const line of text.split('\n').filter(line => line.trim())) {
    const index = line.indexOf('=')
    if (index <= 0) throw new Error('参数请按每行 key=value 填写')
    const key = line.slice(0, index).trim()
    if (Object.hasOwn(result, key)) throw new Error(`参数 ${key} 重复`)
    result[key] = line.slice(index + 1).trim()
  }
  return result
}
export function parameterText(values) { return Object.entries(values || {}).map(([key, value]) => `${key}=${value}`).join('\n') }
