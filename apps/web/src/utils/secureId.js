export function createSecureId(prefix, cryptoApi = globalThis.crypto) {
  if (typeof cryptoApi?.randomUUID === 'function') {
    return `${prefix}-${cryptoApi.randomUUID()}`
  }
  if (typeof cryptoApi?.getRandomValues === 'function') {
    const values = cryptoApi.getRandomValues(new Uint8Array(16))
    return `${prefix}-${Array.from(values, value => value.toString(16).padStart(2, '0')).join('')}`
  }
  throw new Error('当前浏览器不支持安全随机数，请使用现代浏览器访问 HTTPS 或 localhost')
}
