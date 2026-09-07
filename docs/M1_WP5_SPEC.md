# M1 WP5a · SUPERVISED_AUTO 后端 —— 工作包规格

> **目的**:在任何新会话开 WP5a 前,先把本文档读完。文档自包含,包含裁决全文、产品语义、状态机、端点、commit 切分建议。
>
> **本文件是命令式的**,不只是背景资料。开工前逐条对照 §3.0–§3.9。改动清单必须在开工前交;若 ≥ 28 文件,按 commit 1–3 / 4–7 拆成 WP5a-1 / WP5a-2。

## 0. WP 序列(裁决固定,不许重排)

```
WP0  daemon tick                  ✅
WP1  燃烧曲线                     ✅
WP2  Tier 表 + min_tier           ✅
WP3  ScoreWeights + BURN_DOWN     ✅
WP4  UnlimitedPool + opencode-free ✅
WP5a SUPERVISED_AUTO 后端         ← 下一个
WP5b SUPERVISED_AUTO macOS 前端
WP6  Backlog Filler
WP7  周报端点
```

- `fix/stale-badge` 是 WP5b 的一个**待办项提前做了**,不是 WP5b 本身。
- PAID_USAGE 是 **M3**,不许在 M1 提。
- 禁止提"WP5a'"/"WP6'/新的 N 方案"等不在上面 8 个里的工作包。

## 1. 3.0 产品语义(一句话)

在 MANUAL(属主每次点派发)和 ACTIVE(全自动,受 6-gate 约束,仍 DISABLED_BY_DESIGN)之间加中间态:**daemon 按 WP3 推荐器自动选目标,通知属主,宽限期内属主不否决就派发**。"收到通知且没拦" = 同意,所以倒计时只在通知送达后开始。

## 2. 3.1 设置

### 全局

- `SchedulingSettingsView` 加 `mode: str = "MANUAL"`、`selectable_modes: tuple = ("MANUAL","SUPERVISED_AUTO","ACTIVE")`。
- `PUT /v1/settings/scheduling` 接受 `mode`。
- `mode="ACTIVE"` 且 `activation_authority.authorized == False` → **409** `production_active_not_authorized`(fail-closed,**不实现** ACTIVE 行为)。
- 切回 `MANUAL` → 同一事务内所有 `AUTO_PLANNED`/`AUTO_GRACE` 任务回 `READY`,audit `AUTO_ABORTED{reason="mode_changed"}`。

### 项目级(裁决 5)

- `projects` 表 `_ensure_column` 三列:
  - `supervised_auto_allowed INTEGER NOT NULL DEFAULT 0`
  - `unattended_allowed INTEGER NOT NULL DEFAULT 0`
  - `grace_seconds INTEGER NOT NULL DEFAULT 120`
- `SafetyKernelStore.set_project_settings(project_id, *, ...)` 写入。
- `PUT /v1/projects/{id}/settings`(沿用 PUT 模式)。
- `ProjectView` 加三字段(`supervised_auto_allowed` / `unattended_allowed` / `grace_seconds`)。
- 关闭 `supervised_auto_allowed` → 该项目 `AUTO_*` 任务回 `READY`。

### 生效条件(AND)

- `mode == SUPERVISED_AUTO`
- 项目 `supervised_auto_allowed == true`
- `owner_initiated_execution_enabled == true`(默认值不动)

## 3. 3.2 任务状态(只增)

`TaskState` 追加两个值:`AUTO_PLANNED`、`AUTO_GRACE`。`_ALLOWED_TRANSITIONS` 追加:

```
READY → AUTO_PLANNED
AUTO_PLANNED → AUTO_GRACE | READY
AUTO_GRACE → RUNNING | READY
```

`start_dispatched_worker` 加参数 `expected_state: TaskState = READY`,AUTO 路径传 `AUTO_GRACE`(保持原子事务,**不经** READY 中转,避免被下一 tick 重新规划)。

`tasks` 表 `_ensure_column` 四列:

- `auto_decision_id TEXT`
- `auto_grace_deadline_at TEXT`
- `auto_acked_at TEXT`
- `auto_reason TEXT`

`TaskView` 同名四个可选字段(默认 `None`);Swift `TaskView` 四个 `decodeIfPresent` + 正向/向后测试。**WP5a 只加 model,不做 UI**(UI 属 WP5b)。

## 4. 3.3 派发入口抽取(裁决 14)

把 `POST /v1/tasks/{id}/dispatch` handler 里"创建 dispatch 记录 → `dispatch_executor.execute(request_id)` 起线程"那段抽为:

```python
initiate_owner_dispatch(
    store, executor, *,
    task_id, execution_target_id, authority, actor, expected_state
)
```

handler 与 tick 都调它。AUTO 路径传 `authority="SUPERVISED_AUTO"`、`actor="supervised_auto"`。**不**直接调 `start_dispatched_worker`(会绕过 `_admit_quota` 与 dispatch 记录)。

同理,`control_api._collect_dispatch_candidates` + `recommend_owner_dispatch` 的组合抽到 `dispatch_recommendation_service.py`,handler 与 tick 共用,返回 `DispatchRecommendation` + 候选 `DispatchCandidateInput` 列表。

## 5. 3.4 tick step `supervised_auto_step(now)`

注册进 `DaemonSupervisor`(`--control-only` 不注册)。每 tick 步骤:

1. **扫 READY**(且 `scheduling_policy != MANUAL`、项目生效条件满足、`task.min_tier` 合法)→ 调推荐服务。硬条件全部 AND,任一不满足 → 保持 `READY`,`auto_reason` 写原因,audit `AUTO_SKIPPED{reason}`(**同任务同原因去重**,避免每 5 秒一条):
   - top-1 `admitted == true`
   - `quota_state ∈ {AVAILABLE_OBSERVED, AVAILABLE_UNMETERED}`(UNKNOWN / UNCERTAIN_LOCKED / COOLDOWN 一律不自动派发)
   - `evidence_fresh == true`
   - tier ≥ min_tier(推荐器已保证)
   - 执行分支不在受保护列表(复用 execution-repo 现有策略)
   - `SwitchLeaseAuthority.has_active_lease(task_id) == False`(新增公开助手,inline 那条 SQL)

2. 通过 → 事务内:
   - `READY → AUTO_PLANNED`
   - `auto_decision_id` 写入
   - 冻结一条 `RoutingDecision` 到 `/v1/tasks/{id}/routing` 读取的那个存储
     - `authority="SUPERVISED_AUTO"`
     - **只填现有字段**,不改 Gate-R schema
     - **开工前必须核实 `RoutingDecision` 必填字段能否在规划阶段全部给出**,不能则报告
   - `ShadowEvidenceJournal.append_pending(...)`(裁决 15)
   - audit `AUTO_PLANNED{decision_id, target}`
   - 随即 `AUTO_PLANNED → AUTO_GRACE`

3. **倒计时**:
   - `unattended_allowed == true` → 进 `AUTO_GRACE` 时**即写** `auto_grace_deadline_at = now + grace_seconds`
   - `unattended_allowed == false` → `deadline` 留空,等 `POST /v1/tasks/{id}/auto/ack` 时写 `auto_acked_at` 与 `deadline = ack_time + grace_seconds`
   - 未 ack 的任务无限期停在 `AUTO_GRACE`(app 出现即 ack)
   - **但**超过 24h 未 ack → 回 `READY`,audit `AUTO_ABORTED{reason="unacked_timeout"}`

4. **到期**:`now >= deadline` →
   - **重新**调 `_admit_quota`(不用规划时的旧数据)
   - 通过则 `initiate_owner_dispatch(..., expected_state=AUTO_GRACE)`,audit `AUTO_DISPATCHED{decision_id, trigger="grace_expired"}`
   - 失败则回 `READY`,删除 pending shadow,audit `AUTO_ABORTED{reason="admission_failed"}`

5. **幂等**:同一 `now` 调用两次不重复派发、不重复写 pending;所有状态判断以 SQLite 事务为准,不依赖内存。

## 6. 3.5 端点

| 路径 | 行为 | 状态码 |
|---|---|---|
| `POST /v1/tasks/{id}/auto/ack` | `AUTO_GRACE` 且未 ack → 写 ack + deadline;已 ack → 200 幂等;非 `AUTO_GRACE` → 409 | 200 / 409 |
| `POST /v1/tasks/{id}/auto/veto` | `AUTO_PLANNED`/`AUTO_GRACE` → `READY`,`scheduling_policy` 强制置 `MANUAL`,删除 pending shadow,audit `AUTO_VETOED{decision_id, target}` | 200 |
| `POST /v1/tasks/{id}/auto/dispatch-now` | `AUTO_GRACE` → 立即执行 §5 步骤 4,`trigger="dispatch_now"` | 200 / 409 |

`/v1/tasks/{id}/cancel` 对 `AUTO_*` 状态等同 veto(**不**新语义)。

## 7. 3.6 shadow 闭环(裁决 15)

pending 在 worker 跑完后由 `execution_controller.apply_verification_result` 的现有 `shadow_*` 路径 `finalize_pending`——**SUPERVISED_AUTO 每次派发自然产生一条真实 shadow observation**。

**开工前核实**:
1. `PendingShadowObservation` 必填字段能否在规划阶段给全?
2. `apply_verification_result` 如何拿到 `pending_id`?建议存 `auto_decision_id` 同值。

## 8. 3.7 测试(必须全覆盖)

- **状态机**:每条边(含非法边被 `transition_task` 拒绝);`start_dispatched_worker(expected_state=AUTO_GRACE)`。
- **tick**:生效条件三者缺一不动;六个硬条件各一例 SKIPPED 且 reason 正确;`unattended=false` 无 ack 不倒计时、ack 后倒计时;`unattended=true` 立即倒计时;到期复检失败回 READY;到期成功走 `initiate_owner_dispatch` 且 `authority/actor` 正确;幂等;24h 未 ack 超时。
- **mode 切 MANUAL / 项目关闭**:全部回 READY,RUNNING 不受影响。
- **veto 后**:policy 锁 MANUAL 且下一 tick 不再规划。
- **推荐服务抽取后**:`POST /dispatch/recommendation` 现有测试零改动通过。
- **`/v1/tasks/{id}/routing`** 能读到 `SUPERVISED_AUTO` 决策;Gate-R 9 fixture 不动。
- **pending shadow**:规划时写入、veto 时删除、正常执行后 finalize 有一条 observation。
- **控制面**:三个 auto 端点的 200/409;`PUT settings mode=ACTIVE` 409;项目 settings round-trip。
- **Swift**:`TaskView` 四字段、`ProjectView` 三字段、`SchedulingSettingsView.mode/selectableModes` 解码正向/向后;TestDaemon canned body。

## 9. 3.8 不动(白名单)

- `activation_authority` 与 6 gate(裁决 8 的门禁不动)
- `owner_initiated_execution_enabled` 默认值
- Gate-R 已有字段
- admission 语义
- Backlog(WP6)
- 任何 UI(WP5b)

## 10. 3.9 commit 切分建议(7 个,零行为变化 commit 1 优先)

1. `refactor(m1-wp5a): extract initiate_owner_dispatch and dispatch_recommendation_service`(**零行为变化**,所有现有测试不改一行即通过)
2. `feat(m1-wp5a): add SchedulingMode setting and project-level supervised-auto settings`
3. `feat(m1-wp5a): add AUTO_PLANNED / AUTO_GRACE task states and auto columns`
4. `feat(m1-wp5a): supervised_auto_step tick with hard-gated planning, ack-gated grace, and re-admission`
5. `feat(m1-wp5a): auto ack / veto / dispatch-now endpoints and mode-change abort`
6. `feat(m1-wp5a): Swift models for auto task fields, project settings, scheduling mode`
7. `docs(m1-wp5a): ...`

**清单纪律**:
- 开工前先交**改动文件清单**(每条标小节,含 §3 / §7 两项核实结果)。
- 预计 **22–28 个文件**。
- 若估到 ≥ 28,**按 commit 1–3 / 4–7 拆成 WP5a-1 / WP5a-2 两个工作包**各自交清单,**不要等超了再报**。

## 11. 已知裁决引用

- **裁决 5**:项目级设置三列 `supervised_auto_allowed` / `unattended_allowed` / `grace_seconds`。
- **裁决 8**:`activation_authority` 与 6 gate 不动。
- **裁决 14**:派发入口抽取(`initiate_owner_dispatch` + `dispatch_recommendation_service`)。
- **裁决 15**:pending shadow 写入与 finalize,`auto_decision_id` 与 `pending_id` 同值。
- **裁决 §6.6**:单工作包改动 > 30 文件必须停下报告;**本工作包预计 22–28 文件,接近上限,清单必须先交**。
- **裁决 §6.7**:任何 outbound 网络调用必须先问属主,不得 agent 自跑。

## 12. 自检清单(开工前逐条 √)

- [ ] §7 第 1 项核实完成(`PendingShadowObservation` 必填字段能否在规划阶段给全)
- [ ] §7 第 2 项核实完成(`apply_verification_result` 如何拿到 `pending_id`)
- [ ] §5 步骤 2 `RoutingDecision` 必填字段核实完成
- [ ] 改动文件清单已交(每条标 §3.x)
- [ ] 若清单 ≥ 28 文件,已按 WP5a-1 / WP5a-2 拆分
- [ ] WP5a 不动 §9 白名单任一项
- [ ] 测试覆盖 §8 全部小节

---

**文档维护**:M1 后续工作包开工前,把对应裁决全文纳入本目录(`/docs/M1_WP{xx}_SPEC.md`),避免新会话丢上下文。