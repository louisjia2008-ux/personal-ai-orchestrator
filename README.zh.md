# Personal AI Orchestrator(pao)

开源、安全优先的**编码代理与多模型 AI 工作流资源调度器**。

> 状态:**调度决策内核已验证、今天就能用**(Jev 路由,支持 pi / DeepSeek Harness / CLI 三种宿主)· 编排 daemon **pre-alpha**

## 安装与支持平台

- Python 核心/CLI：macOS 或 Linux，Python 3.12。
- JavaScript 适配器：macOS 或 Linux，Node.js 24。
- 原生仪表盘/菜单栏/Widget：macOS 13+，Swift tools 5.9+。
- Windows 目前未支持、未观察。

```bash
git clone https://github.com/louisjia2008-ux/personal-ai-orchestrator.git
cd personal-ai-orchestrator
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/pao --help
```

开发与 clean-clone 验证见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。项目采用
[`MIT License`](LICENSE)，公开发布准备清单见
[`docs/PUBLIC_RELEASE_CHECKLIST.md`](docs/PUBLIC_RELEASE_CHECKLIST.md)。

English | [中文](README.zh.md)

## 今天就能用的:Jev 配额感知模型调度

调度决策层已实现、经真实 API 端到端验证,并提供三种宿主接入:

| 宿主 | 形态 | 位置 | 安装 |
|---|---|---|---|
| [DeepSeek Harness (dsh)](https://github.com/deepseek-ai/deepseek-harness) | bundle 插件 | [`integrations/dsh-plugin/`](integrations/dsh-plugin/) | `dsh plugin --profile <name> add github:louisjia2008-ux/personal-ai-orchestrator` |
| [pi](https://github.com/earendil-works/pi-coding-agent) | 扩展包 | [`integrations/pi-dsh/`](integrations/pi-dsh/) | `pi install git:github.com:louisjia2008-ux/personal-ai-orchestrator` |
| 任意 CLI | 启动脚本 | [`spikes/jev-dispatch/`](spikes/jev-dispatch/) | `pi-jev "任务"` / `dsh "任务"` |

### 路由逻辑

```
任务 ──> 实时探测:ZAI 5h/周窗口 · MiniMax Token Plan · DeepSeek 预付余额
     ──> 硬门(代码):窗口耗尽 / 订阅失效 / 无密钥 / paygo 余额不足
     ──> Jev(TypeSafe System One),一次请求三问并行:
           task_tier (Score 0–3 任务分级) · route (Choice 选路) · defer_acceptable (Noul 可否延迟)
     ──> 策略门(代码):置信度兜底 · tier 下限强制 · 排队建议
     ──> 以选中的 provider/model 执行
```

**代码管规则,Jev 管语义。** 确定性计算(配额比例、压力分级、池共享、tier 下限)绝不交给模型;Jev 只提供普通代码做不了的判断(任务强度多大、该烧哪个健康的池、现在能不能推迟)。

### 烧毁优先(burn-first)经济学

付费 Token Plan 的周限额"不用就作废",因此目标函数最大化付费池的利用率——每个池带一个代码计算的 `burn_value`(周剩余比例 × 重置紧迫度加权)——按量付费池只作最后兜底,由实时余额门控(旗舰门槛 / 整池门槛)。

### 已验证

- 决策层:9/9 场景(池级压力路由、跨池 failover、tier 刚性、置信度兜底)——原始数据在 [`spikes/jev-dispatch/results/`](spikes/jev-dispatch/results/)
- 真实凭证端到端:探测 → Jev → 真实模型执行并返回产出
- dsh 无头集成:seed `minimax-cn/MiniMax-M3` 被逐轮改写到 `zai/glm-5.3`(复杂任务)与 zai 轻档(琐碎任务),经 `agent/request` 瀑布生效

需要环境变量 `TYPESAFE_API_KEY`([获取](https://console.typesafe.ai/keys))。模型凭证留在各宿主的原生存储中。

## 可选的 Telegram 控制 DeepSeek Harness

DSH bundle 现在还包含一个默认关闭的 Telegram 控制器。只有所有者显式
配置专用 Bot Token 环境变量、用户与聊天双白名单、以及一个固定的绝对
工作目录后，才会开放以下窄接口：

```text
/dsh run <自然语言任务>
/dsh status
/dsh cancel
/dsh new
/dsh help
```

控制器使用 Harness 的 `ctx.agents` 接口，工具权限、沙箱与审批仍完全由
所选 Harness profile 控制。它不提供 Telegram shell，也不提供远程绕过审批的
命令。仅安装插件不会连接 Telegram。详见 [DSH 插件安全与配置说明](integrations/dsh-plugin/README.zh.md)。

## 解决什么问题

现代编码代理用户同时拥有多家 provider、多种模型 SKU、订阅制编码套餐、API 钱包和本地模型。

困难的问题早已不是*哪个模型最强*,而是:

> **在要求的质量、延迟、配额消耗与成本下,把任务交给正确的执行目标——同时不削弱仓库安全与确定性验证。**

Personal AI Orchestrator 由两层构成:

1. 宿主持有的**安全内核(Safety Kernel)**:任务/运行状态、worktree 隔离、验证、审批、恢复与审计;
2. 可解释的**模型资源调度器(Model Resource Scheduler)**:模型/目标选择、共享配额池、准入、路由、升级、遥测与 Cost-to-Green 优化。

## 核心架构

```text
OpenCode / CLI / macOS / Telegram / DeskPet
                     |
                     v
         Personal AI Orchestrator Core
+------------------------------------------------+
| Safety Kernel(安全内核)                       |
| - Task / Run 状态                              |
| - Git worktree 隔离                            |
| - 单写者所有权                                  |
| - 确定性验证器                                  |
| - 审批 / 恢复 / 审计                            |
|                                                |
| Model Resource Scheduler(模型资源调度器)      |
| - Provider / Account / Plan                    |
| - 共享配额池                                    |
| - 模型 SKU + 执行目标                          |
| - 能力画像                                      |
| - 硬资格 + 配额准入                             |
| - 确定性排序 / 可解释                           |
| - Cost-to-Green 遥测                            |
+------------------------------------------------+
                     |
              worker/gateway 适配器
                     |
        +------------+-------------+
        |            |             |
      Codex       Claude Code    OpenCode
                                   |
                       GLM / MiniMax / DeepSeek /
                       其他 API 或本地模型
```

## 重要不变量

worker 说 `COMPLETE` **不等于**任务完成。

只有宿主持有的确定性验证通过后任务才能推进。模型路由、配额优化、降级或 UI 控制永远不能绕过这条规则。

## 资源身份

调度器不把厂商、模型、商业套餐和执行路径折叠成一个身份:

```text
Provider != Account != Plan != QuotaPool != ModelSKU != ExecutionTarget
```

`ModelSKU` 是模型/能力的逻辑身份;`ExecutionTarget` 是运行它所用的具体账户/运行时/商业路径。例如:

```text
GLM-5.3
  -> Z.AI Coding Plan(经 OpenCode)
  -> Z.AI PAYG API
```

两个目标能力相同,但配额、计费、可用性与凭证边界完全不同。

`RuntimeVariant` 不是 MVP 必需的一等实体;轻量运行时/变体元数据可先放在 `ExecutionTarget` 上,直到证据证明需要更多复杂性。

见 [`docs/MODEL_RESOURCE_ORCHESTRATION.md`](docs/MODEL_RESOURCE_ORCHESTRATION.md)。

## 约束优先的调度

第一版生产调度器**不做**跨全部模型的全局加权评分,而是三段式:

```text
1. 硬资格
   -> 能力/风险/运行时/支付/配额真值门

2. 配额/任务准入
   -> 预测任务消耗必须装得进可用余量

3. 确定性排序
   -> 质量、成功先验、time/cost-to-green、延迟、
      时间性盈余/保守、池优先级
```

再强的能力分也补不回一次失败的储备、支付、运行时或配额真值约束。

## 配额真值

配额状态被规范化并标注置信度:

```text
AVAILABLE / LIMITED / CRITICAL / EXHAUSTED / UNKNOWN

EXACT      provider 报告的语义清晰的真值
ESTIMATED  由有用但不完整的信号推导
UNKNOWN    无可靠精确值
```

测量/来源方法与置信度分离。`LOCALLY_MEASURED` 描述值怎么得来,本身不代表 `EXACT`。

项目绝不把共享套餐真值伪装成精确的逐模型配额。

### 多重重置窗口

对活跃窗口:

```text
pace = 剩余配额比例 / 剩余时间比例
```

多个窗口同时生效时,未知窗口使**路由** pace 变为未知,而不是被静默丢弃。健康的 5 小时窗不能掩盖未知的周限额。

### Pace 不是准入

临近重置的 `HARVEST` 信号只说明容量相对时间过剩,不证明绝对配额够下一个任务。计量订阅流量使用保守准入检查:

```text
usable_headroom = 最小剩余比例
                - 保留比例
                - 不确定边际

predicted_burn = 任务消耗 p90
               × 消耗乘数
```

预测消耗装不进可用余量就不准入,即使 pace 是 `HARVEST`。

## 可回放性

路由解释必须基于决策时刻已知的事实。注册表把配额成员关系与消耗语义保存为追加式时间事实,配额观测有稳定不可变的快照 ID。路由决策可记录:

```text
catalog_snapshot_id
policy_snapshot_id
quota_snapshot_ids[]
```

后来 provider 的修正不能悄悄改写过去的决策理由。

## 调度器该优化什么

公开基准可作先验,但真实排序应逐渐使用本地观测结果:Pass@1、attempts-to-green、time-to-green、tokens-to-green、quota-to-green、cost-to-green、验证失败率、回归率、上下文/运行时契合度、任务族特化可靠性。

自适应路由只在足够真实遥测之后引入,且只能在已过硬约束的候选间调节排序。

## ACTIVE 路由门

资源调度代码与 Shadow Mode 可以先于完整执行栈开发,但**生产 ACTIVE 路由不因调度器能给出推荐而自动授权**。生产切换前需要:P0 安全内核权威、P1 确定性验证权威、fail-closed 适配器决策验证、安全 BYPASS、跨多个配额重置周期的真实 Shadow 证据、无可接受的验证质量/回归惩罚。

一次性集成 spike 可在会话级 ACTIVE 切换上豁免此门。

## macOS 体验

编排核心保持无头、跨客户端。第一方 macOS 客户端包含三个互补界面:**菜单栏**(状态/安全提交/有界取消)、**完整仪表盘**(NavigationSplitView 各分区)、**桌面/通知中心小组件**(只读快照)。

P4.2 提供本地 app-bundle 构建:

```bash
cd macos/PAOMenuBar
bash scripts/build_app_bundle.sh
open "dist/Personal AI Orchestrator.app"
```

bundle 内含 `Contents/Helpers/pao-daemon`。仪表盘仍是客户端:只读类型化 `/v1` daemon 视图,绝不直接读安全内核 SQLite、凭证或浏览器会话存储。DeskPet 是可选客户端而非依赖。

见 [`docs/MACOS_CONTROL_PLANE.md`](docs/MACOS_CONTROL_PLANE.md)。

## Telegram 与 DeskPet

Telegram 与 DeskPet 复用同一个类型化客户端网关和同一份持久任务状态。网关只提供
submit/status/cancel/approve/report，不存在任意命令或 shell 字段。Telegram 同时校验
用户与 chat 白名单，并用消息派生的稳定 request ID 保证重试幂等。Bot 凭证留在
provider 原生秘密存储中，不进入 PAO 状态或命令参数。

详见 [`docs/TELEGRAM_DESKPET_CLIENTS.md`](docs/TELEGRAM_DESKPET_CLIENTS.md)。

## 安全原则

1. worker 不写主仓库。
2. 每个实现任务有任务专属 Git worktree。
3. 一个 worktree 至多一个活跃写者。
4. 代理不能创建或销毁自己的安全边界。
5. 自然语言提示不能定义可执行的验证命令。
6. 受信项目档案定义 build/test/hygiene 命令。
7. worker 完成与任务完成是两个状态。
8. 未知或不一致状态 fail-closed 为 `BLOCKED`。
9. 凭证留在 provider 原生存储或 macOS 钥匙串;密钥不落明文任务记录。
10. 动态路由不能削弱验证、审批或隔离策略。
11. 除非用户策略显式允许,禁止自动付费超额。
12. 多代理执行由任务复杂度决定,不因模型多而默认开启。
13. 未知的约束性配额窗口不能因另一个窗口看起来健康而被忽略。
14. 时间性 `HARVEST` 不能凌驾绝对任务余量。
15. 重叠且未被取代的资源事实按歧义 fail-closed。

## 规划阶段

| 阶段 | 目标 |
|---|---|
| Spike / PoC | 在一次性工作区上验证 worker 生命周期与安全边界。 |
| P0 安全内核 | 持久任务/运行/审计状态、worktree 管理、写者锁、监督、fail-closed 恢复。 |
| P1 验证 | 受信确定性 build/test/diff 验证与显式 `VERIFIED` 门。 |
| P2 多 worker | provider 多样性、评审分离、严格任务所有权。 |
| P2.5 模型资源注册表 | 模型 SKU、执行目标、共享配额池、时间事实、可解释路由输入。 |
| P3 配额治理 | provider 采集器、不可变配额观测、保留、时间性稀缺与准入输入。 |
| P3.5 Shadow 验证 | 生产 ACTIVE 前跨重置周期对比调度推荐与真实手动选择。 |
| P4 客户端 / macOS | 本地 API、CLI、菜单栏、WidgetKit、仪表盘、DeskPet 集成。 |
| P5 Cost-to-Green | 按模型 SKU/执行目标/语言框架/角色风险的真实任务分析。 |
| P6 自适应调度 | 证据驱动的排序改进,带确定性回滚与硬约束。 |

见 [`docs/ROADMAP.md`](docs/ROADMAP.md) 与 [`docs/SCHEDULER_CORRECTNESS.md`](docs/SCHEDULER_CORRECTNESS.md)。

## 初始技术方向

Python 3.12+、`asyncio`、Pydantic 类型模型、SQLite + WAL、ACP v1(按需)、Git worktrees、pytest + 一次性 Git 仓库 + 假 worker、结构化 JSON 审计日志优先(OpenTelemetry 后置)、Unix Domain Socket / 类型化本地 API、macOS 钥匙串、SwiftUI + WidgetKit(可选客户端)。

MVP 刻意避免 PostgreSQL、Redis、Kafka、Kubernetes、公有 SaaS API、向量数据库、黑盒 ML 路由器与分布式调度。

## 开发策略

变更应小、可审计、面向里程碑。每个里程碑让仓库处于确定性可测试状态。见 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)。

## 开源协议

本项目采用 [MIT License](LICENSE) 发布。
