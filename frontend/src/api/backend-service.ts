import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  ClaimRecord,
  ConfirmationRecord,
  EntryRow,
  OverviewResult,
  PageResult,
  ScopeView,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ── 归集归属核对：列表、范围视图、确认纪要 ─────────────────

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.detail ?? fallback)
  return payload as T
}

export async function listBatchClaims(batchId: number): Promise<ClaimRecord[]> {
  const response = await request(`/api/capture_batch/${batchId}/claims`)
  const payload = await readJson<{ items: ClaimRecord[] }>(response, '归属读取失败')
  return payload.items
}

export async function loadBatchScope(batchId: number, execution: string): Promise<ScopeView> {
  const query = new URLSearchParams({ execution }).toString()
  const response = await request(`/api/capture_batch/${batchId}/scope?${query}`)
  const payload = await readJson<{ data: ScopeView }>(response, '归属范围读取失败')
  return payload.data
}

export async function listBatchConfirmations(
  batchId: number,
  execution?: string,
): Promise<ConfirmationRecord[]> {
  const query = execution ? `?${new URLSearchParams({ execution }).toString()}` : ''
  const response = await request(`/api/capture_batch/${batchId}/confirmations${query}`)
  const payload = await readJson<{ items: ConfirmationRecord[] }>(response, '确认记录读取失败')
  return payload.items
}

export async function withdrawBatchScope(
  batchId: number,
  execution: string,
  items: string[],
  reason: string,
): Promise<{ data: { missing_scope: string[]; suspended_handoffs: { 交接编号: string }[] } }> {
  const response = await request(`/api/capture_batch/${batchId}/withdrawals`, {
    method: 'POST',
    body: JSON.stringify({ execution, items, reason }),
  })
  return await readJson(response, '撤回归属失败')
}

export async function supplementBatchScope(
  batchId: number,
  execution: string,
  items: string[],
  reason: string,
): Promise<{ data: { supplemented: string[]; remaining_missing: string[] } }> {
  const response = await request(`/api/capture_batch/${batchId}/supplements`, {
    method: 'POST',
    body: JSON.stringify({ execution, items, reason }),
  })
  return await readJson(response, '补采失败')
}
