import test from 'node:test'
import assert from 'node:assert/strict'
import { mayRetryConfirmation, safetyRequest } from '../src/utils/deliverySafety.js'

test('B action requires all A receipts, live runtime and B-only permission', () => {
  const record = { receiptMode: 'platform', contentState: 'DELIVERED', canRetryConfirmation: true, confirmationState: 'unknown' }
  assert.equal(mayRetryConfirmation(record, { actionsAvailable: true }), true)
  for (const change of [{ receiptMode: 'mock' }, { contentState: 'UNKNOWN' }, { confirmationState: 'sending' }, { confirmationState: 'confirmed' }, { canRetryConfirmation: false }]) {
    assert.equal(mayRetryConfirmation({ ...record, ...change }, { actionsAvailable: true }), false)
  }
  assert.equal(mayRetryConfirmation(record, null), false)
})

test('an ambiguous action response is surfaced and never automatically replayed', async () => {
  const original = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => { calls++; throw new Error('network timeout') }
  try {
    await assert.rejects(safetyRequest('/api/desktop/safety/records/1/retry-confirmation', { method: 'POST' }), /timeout/)
    assert.equal(calls, 1)
  } finally { globalThis.fetch = original }
})
