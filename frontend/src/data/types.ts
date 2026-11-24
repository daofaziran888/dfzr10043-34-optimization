/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 共享归集回执下，单个执行的归属记录。 */
export type ClaimRecord = {
  id: number
  回执编号: string
  批次编号: string
  执行编号: string
  领取范围: string[]
  确认范围: string[]
  撤回范围: string[]
  补采范围: string[]
  状态: string
  领取时间: string
  最近确认: string
  确认编号: string
}

/** 一次确认纪要：范围在哪次确认中被收窄，靠它复核。 */
export type ConfirmationRecord = {
  确认编号: string
  批次编号: string
  执行编号: string
  确认类型: string
  确认前范围: string[]
  确认后范围: string[]
  撤回项: string[]
  补采项: string[]
  收窄说明: string
  关联交接: string
  确认时间: string
}

export type HandoffRecord = {
  交接编号: string
  批次编号: string
  执行编号: string
  目标系统: string
  交接范围: string[]
  状态: string
  挂起原因: string
  挂起时间: string
  恢复次数: number
  交接时间: string
}

export type DeliveryRecord = {
  交接编号: string
  执行编号: string
  目标系统: string
  交接范围: string[]
  核对摘要: string
  交接时间: string
}

export type ScopeView = {
  回执: { 回执编号: string; 批次编号: string; 上游来源: string; 数据项: string[] } | null
  归属: ClaimRecord | null
  当前范围: string[]
  缺失范围: string[]
  确认记录: ConfirmationRecord[]
  交接记录: HandoffRecord[]
  已交接: DeliveryRecord[]
}
