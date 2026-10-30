import test from 'node:test'
import assert from 'node:assert/strict'
import { createSecureId } from '../src/utils/secureId.js'

test('使用密码学随机源生成不同的操作标识', () => {
  const ids = new Set(Array.from({ length: 100 }, () => createSecureId('publish')))
  assert.equal(ids.size, 100)
  assert.ok([...ids].every(id => id.startsWith('publish-')))
})

test('没有 randomUUID 时使用 getRandomValues，缺少随机源时拒绝操作', () => {
  const cryptoApi = { getRandomValues: values => values.fill(255) }
  assert.equal(createSecureId('publish', cryptoApi), `publish-${'ff'.repeat(16)}`)
  assert.throws(() => createSecureId('publish', {}), /安全随机数/)
})
