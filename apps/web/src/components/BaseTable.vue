<template>
  <table class="base-table">
    <thead>
      <tr>
        <th v-if="selectable" class="col-select" @click="onHeaderSelectClick">
          <input
            type="checkbox"
            class="bt-checkbox"
            :checked="allSelected"
            :indeterminate.prop="someSelected"
            :disabled="!rows || rows.length === 0"
            aria-label="全选"
            @click.stop
            @change="toggleAll"
          />
        </th>
        <th
          v-for="c in columns"
          :key="c.key"
          :class="{ sortable: c.sortable, 'sort-active': isSortActive(c), 'sort-asc': isSortDir(c, 'asc'), 'sort-desc': isSortDir(c, 'desc') }"
          @click="onHeaderClick(c)"
        >
          <span class="th-text">{{ c.title }}</span>
          <span v-if="c.sortable" class="sort-indicator" aria-hidden="true">{{ sortIndicatorText(c) }}</span>
        </th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="!rows?.length">
        <td :colspan="(columns?.length || 1) + (selectable ? 1 : 0)">
          <slot name="empty">
            <div class="table-empty">暂无数据</div>
          </slot>
        </td>
      </tr>
      <tr
        v-for="(row, idx) in rows"
        :key="resolveRowKey(row, idx)"
        :class="rowClass?.(row, idx) || ''"
        :tabindex="rowIsInteractive ? 0 : undefined"
        @click="activateRow(row)"
        @keydown.enter="activateRow(row)"
        @keydown.space.prevent="activateRow(row)"
      >
        <td v-if="selectable" class="col-select" @click.stop="onRowSelectClick(row, idx, $event)">
          <input
            type="checkbox"
            class="bt-checkbox"
            :checked="isRowSelected(row, idx)"
            :aria-label="'选择第 ' + (idx + 1) + ' 行'"
            @click.stop
            @change="toggleRow(row, idx)"
          />
        </td>
        <td v-for="c in columns" :key="c.key">
          <slot :name="c.key" :row="row">{{ row[c.key] }}</slot>
        </td>
      </tr>
    </tbody>
  </table>
</template>
<script setup>
import { computed, getCurrentInstance } from 'vue'

const fallbackKeys = new WeakMap()
let fallbackKeySequence = 0

function defaultRowKey(row, idx) {
  for (const key of ['id', 'key', 'uuid', 'taskId', 'orderNo', 'accountId']) {
    const value = row?.[key]
    if (value !== undefined && value !== null && value !== '') return `${key}:${value}`
  }
  if (row && typeof row === 'object') {
    if (!fallbackKeys.has(row)) fallbackKeys.set(row, `object:${++fallbackKeySequence}`)
    return fallbackKeys.get(row)
  }
  return `value:${String(row)}:${idx}`
}

const props = defineProps({
  columns: Array,
  rows: Array,
  rowClass: Function,
  // 是否开启行选择（opt-in，默认关闭，向后兼容）
  selectable: { type: Boolean, default: false },
  // 行唯一标识函数，用于 v-model:selectedKeys
  rowKey: { type: Function, default: null },
  rowInteractive: { type: Boolean, default: false },
  // 选中行的 key 数组，配合 v-model:selectedKeys 使用
  selectedKeys: { type: Array, default: () => [] },
  // 当前排序字段（与 column.sortField 匹配）
  sortField: { type: String, default: '' },
  // 当前排序方向：asc / desc
  sortOrder: { type: String, default: '' }
})
const emit = defineEmits(['row-click', 'update:selectedKeys', 'header-click'])
const instance = getCurrentInstance()
const rowIsInteractive = computed(() => (
  props.rowInteractive || typeof instance?.vnode?.props?.onRowClick === 'function'
))

function resolveRowKey(row, idx) {
  return props.rowKey ? props.rowKey(row, idx) : defaultRowKey(row, idx)
}

const rowKeys = computed(() => (props.rows || []).map((r, i) => resolveRowKey(r, i)))
const selectedSet = computed(() => new Set(props.selectedKeys))

function isRowSelected(row, idx) {
  return selectedSet.value.has(resolveRowKey(row, idx))
}

const allSelected = computed(() =>
  props.rows && props.rows.length > 0 && rowKeys.value.every(k => selectedSet.value.has(k))
)
const someSelected = computed(() => {
  const sel = rowKeys.value.filter(k => selectedSet.value.has(k)).length
  return sel > 0 && sel < rowKeys.value.length
})

function toggleAll(e) {
  if (e.target.checked) {
    emit('update:selectedKeys', [...rowKeys.value])
  } else {
    emit('update:selectedKeys', [])
  }
}
function toggleRow(row, idx) {
  const key = resolveRowKey(row, idx)
  const set = new Set(props.selectedKeys)
  if (set.has(key)) set.delete(key)
  else set.add(key)
  emit('update:selectedKeys', Array.from(set))
}
function onHeaderClick(column) {
  if (column && column.sortable) emit('header-click', column)
}
function isSortActive(column) {
  return !!column?.sortField && column.sortField === props.sortField
}
function isSortDir(column, dir) {
  return isSortActive(column) && props.sortOrder === dir
}
function sortIndicatorText(column) {
  if (!isSortActive(column)) return '↕'
  return props.sortOrder === 'asc' ? '↑' : '↓'
}
function activateRow(row) {
  if (rowIsInteractive.value) emit('row-click', row)
}
// 点击表头选择列容器（非 checkbox 本身）时切换全选
function onHeaderSelectClick(e) {
  if (e.target.type === 'checkbox') return
  if (allSelected.value) {
    emit('update:selectedKeys', [])
  } else {
    emit('update:selectedKeys', [...rowKeys.value])
  }
}
// 点击行选择列容器（非 checkbox 本身）时切换该行选中状态
function onRowSelectClick(row, idx, e) {
  if (e.target.type === 'checkbox') return
  toggleRow(row, idx)
}
</script>
<style scoped>
.col-select {
  width: 44px;
  text-align: center;
  white-space: nowrap;
  cursor: pointer;
}
.bt-checkbox {
  width: 16px;
  height: 16px;
  cursor: pointer;
  vertical-align: middle;
}
.base-table tbody tr[tabindex="0"]:focus-visible {
  outline: 3px solid rgba(37, 99, 235, 0.3);
  outline-offset: -3px;
}
.base-table th.sortable {
  cursor: pointer;
  user-select: none;
  transition: color .12s;
}
.base-table th.sortable:hover {
  color: #334155;
}
.base-table th.sortable .th-text {
  display: inline;
}
.base-table th.sortable .sort-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  margin-left: 4px;
  font-size: 12px;
  color: #cbd5e1;
  line-height: 1;
}
.base-table th.sortable.sort-active .sort-indicator {
  color: var(--primary, #2563eb);
}
.base-table th.sortable.sort-active {
  color: #1e293b;
}
</style>
