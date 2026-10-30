import test from 'node:test'
import assert from 'node:assert/strict'
import { importDeliveryCards } from '../src/utils/deliveryCardImport.js'

const goods = { id: 91, title: '教程激活码' }
const formFor = () => ({ cardSource: 'direct', cardKeysText: 'CARD-A----PASS-A\nCARD-B,PASS-B\nCARD-C\tPASS-C\nCARD-D', alertThreshold: 0 })
const counts = (successCount = 4, duplicateCount = 0, failCount = 0) => ({ data: { successCount, duplicateCount, failCount } })

test('原创建接口返回数字 ID 后，将卡密导入同一分组并切换为已有库存', async () => {
  const form = formFor()
  const calls = []
  const id = await importDeliveryCards(form, goods, {
    createGroup: async data => { calls.push(data); return { code: 200, data: 27 } },
    importItems: async (groupId, payload) => { calls.push({ groupId, payload }); return counts() },
  })
  assert.equal(id, 27)
  assert.equal(calls[0].alertThreshold, 0)
  assert.equal(calls[1].groupId, 27)
  assert.deepEqual(calls[1].payload.items, [
    { content: 'CARD-A----PASS-A', cardContent: 'CARD-A', password: 'PASS-A' },
    { content: 'CARD-B,PASS-B', cardContent: 'CARD-B', password: 'PASS-B' },
    { content: 'CARD-C\tPASS-C', cardContent: 'CARD-C', password: 'PASS-C' },
    { content: 'CARD-D' },
  ])
  assert.equal(form.cardGroupId, 27)
  assert.equal(form.cardSource, 'existing')
  assert.equal(form.cardKeysText, '')
})

test('入库回执丢失后重试复用分组，已有卡密以重复记录确认', async () => {
  const form = formFor()
  let creates = 0, imports = 0
  const api = {
    createGroup: async () => { creates++; return { data: 27 } },
    importItems: async id => {
      assert.equal(id, 27)
      if (++imports === 1) throw new Error('网络超时，入库回执丢失')
      return counts(0, 4)
    },
  }
  await assert.rejects(importDeliveryCards(form, goods, api), /超时/)
  assert.equal(form._directImportGroupId, 27)
  assert.equal(form.cardSource, 'direct')
  assert.ok(form.cardKeysText)
  await importDeliveryCards(form, goods, api)
  assert.equal(creates, 1)
  assert.equal(imports, 2)
  assert.equal(form.cardGroupId, 27)
})

test('部分入库失败不绑定发货规则，修正重试仍在原分组中完成', async () => {
  const form = formFor()
  let creates = 0, imports = 0
  const api = {
    createGroup: async () => { creates++; return { data: { id: 27 } } },
    importItems: async () => ++imports === 1 ? counts(3, 0, 1) : counts(1, 3),
  }
  await assert.rejects(importDeliveryCards(form, goods, api), /1 条卡密未入库/)
  assert.equal(form.cardSource, 'direct')
  assert.equal(form.cardGroupId, undefined)
  await importDeliveryCards(form, goods, api)
  assert.equal(creates, 1)
  assert.equal(form.cardSource, 'existing')
})

test('分组创建请求结果不明时不盲目创建第二个分组', async () => {
  const form = formFor()
  let creates = 0
  const api = {
    createGroup: async () => { creates++; throw new Error('创建请求超时') },
    importItems: async () => assert.fail('分组尚未确认，不应入库'),
  }
  await assert.rejects(importDeliveryCards(form, goods, api), /超时/)
  await assert.rejects(importDeliveryCards(form, goods, api), /不会自动重新创建/)
  assert.equal(creates, 1)
  assert.ok(form.cardKeysText)
})

test('无效分组编号和不完整入库回执均不能绑定规则', async () => {
  const invalid = formFor()
  await assert.rejects(importDeliveryCards(invalid, goods, {
    createGroup: async () => ({ data: null }),
    importItems: async () => assert.fail('编号无效，不应调用入库'),
  }), /有效 ID/)
  for (const result of [{ data: null }, counts(1), counts(-1, 5), counts(1, 1, 0.5)]) {
    const form = formFor()
    await assert.rejects(importDeliveryCards(form, goods, {
      createGroup: async () => ({ data: 27 }), importItems: async () => result,
    }), /无法确认/)
    assert.equal(form.cardSource, 'direct')
    assert.ok(form.cardKeysText)
  }
})
