function createdGroupId(response) {
  const value = response?.data?.id ?? response?.data ?? response?.id
  const id = Number(value)
  if (!Number.isSafeInteger(id) || id <= 0) {
    throw new Error('创建卡密分组未返回有效 ID，请先到卡密仓库核对，不要重复创建')
  }
  return id
}

function cardItem(line) {
  for (const separator of ['----', ',', '\t']) {
    const index = line.indexOf(separator)
    if (index > 0) return { content: line, cardContent: line.slice(0, index), password: line.slice(index + separator.length) }
  }
  return { content: line }
}

// Keep the confirmed group on the existing form so a retry uses the same stock.
export async function importDeliveryCards(form, goods, { createGroup, importItems }) {
  const lines = String(form.cardKeysText || '').split(/\n+/).map(line => line.trim()).filter(Boolean)
  if (!lines.length) throw new Error('直接录入卡密时至少输入一条卡密。')
  let groupId = form._directImportGroupId
  if (!groupId) {
    if (form._directImportCreateUnknown) {
      throw new Error('卡密分组创建结果尚未核对，请先查看卡密仓库，再选择已有分组；不会自动重新创建')
    }
    form._directImportCreateUnknown = true
    const result = await createGroup({
      groupName: `商品-${goods.title || goods.id}-卡密组`,
      cardType: 'unique', alertThreshold: form.alertThreshold ?? 5, status: 1,
      remark: `由自动发货配置自动创建，归属商品ID：${goods.id}，标题：${goods.title || ''}`,
    })
    groupId = createdGroupId(result)
    form._directImportGroupId = groupId
    form._directImportCreateUnknown = false
  }
  const result = await importItems(groupId, { items: lines.map(cardItem) })
  const counts = result?.data
  const keys = ['successCount', 'duplicateCount', 'failCount']
  if (!counts || keys.some(key => !Number.isInteger(counts[key]) || counts[key] < 0)
      || keys.reduce((sum, key) => sum + counts[key], 0) !== lines.length) {
    throw new Error(`分组 ${groupId} 的卡密导入结果无法确认，尚未绑定发货规则；请先核对卡密仓库`)
  }
  if (counts.failCount) {
    throw new Error(`分组 ${groupId} 有 ${counts.failCount} 条卡密未入库，尚未绑定发货规则；修正后再次保存会复用该分组`)
  }
  // If saving the rule later fails, subsequent saves must not import again.
  form.cardGroupId = groupId
  form.cardSource = 'existing'
  form.cardKeysText = ''
  form._directImportGroupId = null
  return groupId
}
