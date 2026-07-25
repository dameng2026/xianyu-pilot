import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeRemoteMessagesWithLocalAttempts } from '../src/utils/mobileMessagesSafety.js'

test('用同一平台消息标识合并移动端本地发送项与远端回写', () => {
  const messages = mergeRemoteMessagesWithLocalAttempts(
    [{ pnmId: 'platform-message-1', sid: 'conversation-1', direction: 'OUT', content: '11' }],
    [{ id: 'platform-message-1', sid: 'conversation-1', direction: 'OUT', content: '11', sendStatus: 'sent', idempotencyKey: 'mobile-text-1' }],
  )

  assert.equal(messages.length, 1)
  assert.equal(messages[0].pnmId, 'platform-message-1')
  assert.equal(messages[0].sendStatus, 'sent')
})

test('忽略暂存标识且不保留已经由远端回写确认的本地发送项', () => {
  const messages = mergeRemoteMessagesWithLocalAttempts(
    [{ pnmId: 'platform-message-1', sid: 'conversation-1', direction: 'OUT', content: '11' }],
    [{ id: 'temp_1', pnmId: 'platform-message-1', sid: 'conversation-1', direction: 'OUT', content: '11', sendStatus: 'sent', idempotencyKey: 'mobile-text-1' }],
  )

  assert.equal(messages.length, 1)
  assert.equal(messages[0].id, 'temp_1')
  assert.equal(messages[0].pnmId, 'platform-message-1')
})
