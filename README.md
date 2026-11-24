# 织流工坊 · 网页流程自动化与RPA采集执行平台

面向RPA流程模板编排、元素定位漂移检测、机器人节点调度、执行步骤轨迹、凭据轮换、文件归集、采集批次确认与下游交接的流程自动化采集执行平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   ├── app/scope.py             共享回执的归属台账（领用/确认/撤回/补采/交接范围）
│   └── app/store.py             内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 流程模板 | `flow_template` | 流程模板 | 模板编号、流程名称、起始地址 |
| 元素定位 | `element_locator` | 定位策略 | 定位编号、所属模板、元素名称 |
| 机器人节点 | `robot_node` | 执行节点 | 节点编号、节点名称、运行环境 |
| 流程执行 | `flow_run` | 执行实例 | 执行编号、关联模板、执行节点 |
| 步骤轨迹 | `step_trace` | 步骤轨迹 | 轨迹编号、关联执行、步骤序号 |
| 任务队列 | `task_queue` | 排队任务 | 队列编号、关联模板、优先级 |
| 凭据保管 | `credential_vault` | 登录凭据 | 凭据编号、适用站点、账号标识 |
| 表单字段 | `form_field` | 填充字段 | 字段编号、关联模板、表单名称 |
| 文件归集 | `file_drop` | 归集文件 | 归集编号、关联执行、文件名称 |
| 识别区域 | `ocr_region` | 识别区域 | 区域编号、关联模板、区域名称 |
| 排程计划 | `schedule_plan` | 排程计划 | 计划编号、关联模板、触发周期 |
| 异常规则 | `exception_rule` | 处置规则 | 规则编号、匹配特征、判定条件 |
| 重试策略 | `retry_policy` | 重试策略 | 策略编号、关联规则、最大次数 |
| 采集批次 | `capture_batch` | 采集批次 | 批次编号、关联执行、记录行数 |
| 字段映射 | `field_mapping` | 映射规则 | 映射编号、来源字段、目标字段 |
| 数据落库 | `data_landing` | 落库记录 | 落库编号、关联批次、目标存储 |
| 审计轨迹 | `audit_trail` | 审计条目 | 条目编号、操作对象、操作类型 |
| 资源额度 | `resource_pool` | 资源额度 | 额度编号、资源类型、总额度 |
| 版本发布 | `version_release` | 发布版本 | 发布编号、关联模板、版本号 |
| 运行指标 | `metric_board` | 指标读数 | 读数编号、统计维度、统计窗口 |
| 抽检样本 | `sample_check` | 抽检样本 | 样本编号、关联批次、抽样比例 |
| 交接包 | `handoff_pack` | 交接包 | 交接编号、关联批次、目标系统 |

## 共享归集回执的归属隔离

同一份上游来源可以被多个执行共享：来源数据只有一份（一个采集批次 / 回执），
但每个执行的领取范围、确认记录、撤回记录和已交接清单彼此独立，互不覆盖。

- 台账层 `backend/app/scope.py` 按 `(回执, 执行)` 维度维护归属生命周期：
  领用即确认 → 可多次收窄 → 可选补采 → 按实际确认范围交接。
- 去重只拒绝“同一执行对同一来源的相同数据项”这种真正的重复回执；
  来源相同但执行不同，会复用同一批次/回执并登记各自的独立归属，
  不会把合法归属当作重复丢掉。
- 撤回只收窄发起撤回的执行，并挂起该执行已领取、包含被撤回项的交接；
  同回执下其他执行的合法范围不受影响，也不需要重采。
- 撤回后旧领用不能再带出被撤回项（返回可读拒绝）；缺失范围只能通过补采恢复，
  补采只针对该执行实际缺的部分。
- 两次确认记录都保留：`确认前范围` / `确认后范围` / `撤回项` / `补采项` / 收窄说明，
  值班员可复核范围在哪次确认中收窄。

接口（均返回 `{ ok, ... }`，领域错误以 409 + `detail` 返回）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/capture_batch/{id}/claims` | 列出同一回执下所有执行的归属 |
| GET | `/api/capture_batch/{id}/scope?execution=A` | 当前范围、缺失范围、确认纪要、交接清单 |
| GET | `/api/capture_batch/{id}/confirmations` | 已确认/被收窄的确认记录 |
| POST | `/api/capture_batch/{id}/claims` | 某执行领用数据项（领用即确认） |
| POST | `/api/capture_batch/{id}/withdrawals` | 撤回某执行的部分归属并挂起受影响交接 |
| POST | `/api/capture_batch/{id}/supplements` | 就某执行实际缺失部分补采 |
| POST | `/api/handoff_pack/{batch_id}/requests` | 按某执行当前确认归属发起交接 |
| POST | `/api/handoff_pack/{code}/resume` | 继续被挂起的原交接请求，范围收敛 |
| POST | `/api/handoff_pack/{code}/deliver` | 确认交接（撤回项不能带出） |
| GET | `/api/handoff_pack/{code}/scope` | 核对交接范围与已交接清单 |

交接包把 `交接数据项` / `执行编号` / `交接状态` 写回批次和交接包行，
采集批次列表额外展示 `回执编号`、`归集数据项`、`归属执行` 和 `最新确认编号`。
前端采集批次页点选批次后，右侧面板可直接按执行核对范围、撤回、补采并查看确认纪要。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据采集、爬虫与RPA
