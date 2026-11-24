<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
          :class="{ 'row-selected': isSharedBatch && selectedBatchId === Number(row.id) }"
          @click="isSharedBatch && selectBatch(Number(row.id))"
        >
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click.stop="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 共享归集回执：按执行核对归属、确认纪要、交接清单 -->
    <section v-if="isSharedBatch" class="scope-panel" data-testid="scope-panel">
      <header class="scope-head">
        <div>
          <h3>共享回执归属核对</h3>
          <p class="page-desc">
            同一来源可被多个执行共用；每个执行的归属、确认纪要与撤回记录独立追查。
            点选上方批次后，按执行核对当前范围、缺失范围与已交接清单。
          </p>
        </div>
        <label class="scope-exec">
          <span>执行编号</span>
          <input
            v-model="scopeExecution"
            data-testid="scope-execution"
            placeholder="如 EXEC-A"
            @change="reloadScope"
          />
        </label>
      </header>

      <p v-if="!selectedBatchId" class="empty-state">请选择一条采集批次查看归属</p>
      <template v-else>
        <div class="scope-grid">
          <article class="scope-card">
            <h4>本回执的执行归属</h4>
            <ul class="scope-claims">
              <li v-for="claim in claims" :key="claim.id" :data-testid="`claim-${claim.执行编号}`">
                <button class="link" type="button" @click="pickExecution(claim.执行编号)">
                  {{ claim.执行编号 }}
                </button>
                <span class="tag">{{ claim.状态 }}</span>
                <span class="scope-items">{{ claim.确认范围.join('、') || '—' }}</span>
              </li>
              <li v-if="!claims.length" class="empty-state">暂无执行领用</li>
            </ul>
          </article>

          <article class="scope-card">
            <h4>当前范围 / 缺失范围</h4>
            <p>当前可交接：<strong>{{ scopeView?.当前范围.join('、') || '—' }}</strong></p>
            <p>已撤回待补：<strong class="error-text">{{ scopeView?.缺失范围.join('、') || '—' }}</strong></p>
            <p>已交接清单：
              <span v-for="item in scopeView?.已交接 || []" :key="item.交接编号" class="tag ok">
                {{ item.交接编号 }}：{{ item.交接范围.join('、') }}
              </span>
              <span v-if="!scopeView?.已交接?.length">—</span>
            </p>
            <div class="scope-actions">
              <input v-model="scopeItems" data-testid="scope-items" placeholder="数据项，逗号分隔" />
              <button class="btn" data-testid="withdraw" type="button" @click="withdrawScope">撤回</button>
              <button class="btn ghost" data-testid="supplement" type="button" @click="supplementScope">补采</button>
            </div>
            <p v-if="scopeMessage" class="scope-message">{{ scopeMessage }}</p>
          </article>
        </div>

        <article class="scope-card">
          <h4>确认纪要（可复核范围在哪次确认中收窄）</h4>
          <table class="data-table">
            <thead>
              <tr>
                <th>确认编号</th><th>类型</th><th>确认前</th><th>确认后</th>
                <th>撤回项</th><th>补采项</th><th>说明</th><th>时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in scopeView?.确认记录 || []" :key="row.确认编号">
                <td>{{ row.确认编号 }}</td>
                <td>{{ row.确认类型 }}</td>
                <td>{{ row.确认前范围.join('、') || '—' }}</td>
                <td>{{ row.确认后范围.join('、') || '—' }}</td>
                <td>{{ row.撤回项.join('、') || '—' }}</td>
                <td>{{ row.补采项.join('、') || '—' }}</td>
                <td>{{ row.收窄说明 || '—' }}</td>
                <td>{{ row.确认时间 }}</td>
              </tr>
              <tr v-if="!scopeView?.确认记录?.length">
                <td colspan="8" class="empty-state">该执行还没有确认记录</td>
              </tr>
            </tbody>
          </table>
        </article>
      </template>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  listBatchClaims,
  listEntries,
  loadBatchScope,
  moduleMeta,
  runAction as applyAction,
  supplementBatchScope,
  withdrawBatchScope,
} from '@/api/backend-service'
import type { ClaimRecord, EntryRow, ScopeView } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const isSharedBatch = meta.key === 'capture_batch'
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

// 共享回执归属核对状态
const selectedBatchId = ref<number | null>(null)
const scopeExecution = ref('EXEC-A')
const claims = ref<ClaimRecord[]>([])
const scopeView = ref<ScopeView | null>(null)
const scopeItems = ref('')
const scopeMessage = ref('')
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
    if (isSharedBatch && selectedBatchId.value === null && payload.items.length) {
      await selectBatch(Number(payload.items[0].id))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

async function selectBatch(batchId: number) {
  selectedBatchId.value = batchId
  scopeMessage.value = ''
  await reloadScope()
}

async function reloadScope() {
  if (selectedBatchId.value === null) return
  try {
    claims.value = await listBatchClaims(selectedBatchId.value)
    scopeView.value = await loadBatchScope(selectedBatchId.value, scopeExecution.value)
  } catch (error) {
    scopeMessage.value = error instanceof Error ? error.message : '归属读取失败'
  }
}

async function pickExecution(execution: string) {
  scopeExecution.value = execution
  await reloadScope()
}

function parseItems(): string[] {
  return scopeItems.value
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function withdrawScope() {
  if (selectedBatchId.value === null) return
  scopeMessage.value = ''
  try {
    const result = await withdrawBatchScope(
      selectedBatchId.value, scopeExecution.value, parseItems(), '值班员撤回归属')
    const suspended = result.data.suspended_handoffs.map((item) => item.交接编号)
    scopeMessage.value = `已撤回，缺失范围：${result.data.missing_scope.join('、') || '—'}` +
      (suspended.length ? `；挂起交接：${suspended.join('、')}` : '')
    await reload()
    await reloadScope()
  } catch (error) {
    scopeMessage.value = error instanceof Error ? error.message : '撤回失败'
  }
}

async function supplementScope() {
  if (selectedBatchId.value === null) return
  scopeMessage.value = ''
  try {
    const result = await supplementBatchScope(
      selectedBatchId.value, scopeExecution.value, parseItems(), '值班员补采缺失范围')
    scopeMessage.value = `已补采：${result.data.supplemented.join('、')}；` +
      `仍缺失：${result.data.remaining_missing.join('、') || '—'}`
    await reloadScope()
  } catch (error) {
    scopeMessage.value = error instanceof Error ? error.message : '补采失败'
  }
}

onMounted(reload)
</script>
