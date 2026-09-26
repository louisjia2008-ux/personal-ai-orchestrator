# Jev dispatch spike — 验证结论(2026-09-24,当前世代模型版)

验证目标:Jev(TypeSafe System One, jev-1.13.0)能否作为 pao 调度决策的
判断层——pi 下达任务后自动选择执行模型,并把 FIVE_HOUR / WEEKLY 限额
压力纳入决策。原始数据:`jev_dispatch_results.json`、
`jev_dispatch_e2e_results.json`。

## 端到端闭环(已实测跑通,`run_e2e.py`)

真实链路:**pi 任务 → 活体配额探测 → Jev 决策 → 选中模型真实执行 → 产出**。

- 配额来源:ZAI `quota/limit` 实时返回(短期窗 ~26% / 周窗 ~59%,LOW);
  MiniMax Token Plan 实时返回 `2062 no active subscription`
  → 硬门用真实理由拦截整个 MM 池(非模拟)。
- 凭证:运行时读 opencode auth store,不落盘、不复制。
- 执行:ZAI Anthropic 兼容端点真实调用。

| 运行 | 任务 | Jev 分级 | 决策 | 执行结果 |
| --- | --- | --- | --- | --- |
| A | 线程安全限流器 | 1.2 | glm-5.3-flash(conf 0.94) | ✅ 16.8s 产出可用代码 |
| B | 给命令写注释 | 0.0 | glm-5.3-flash(conf 1.0) | ✅ 3.6s 产出 |
| C | T3 刚性+合成周 CRITICAL | 2.8 | glm-5.3(conf 0.67) | ✅ 25.1s 产出幂等方案(不变量+恢复路径) |

C 说明:MM 无订阅(真实)+ T3 刚性 → 唯一 T3 是 CRITICAL 但未耗尽的
glm-5.3,Jev 守住 pin 选择它,行为符合设计(耗尽才硬拦,CRITICAL 仅建议)。

执行侧工程注意:glm-5.3 默认 thinking=max,300–4096 token 会被思考
吃光导致空 text,旗舰需 8192 预算 + ≥300s 超时;flash 用 1600 即可。

> 版本记录:首版用虚构 Claude 订阅夹具;第二版换成真实套餐拓扑但模型
> 名仍是旧世代占位(glm-4.7/m2);本版按 docs.z.ai 与 minimax.io
> (2026-09 核实)更新为当前世代 SKU。结论方向不变,本版为准。

## 候选拓扑(当前世代,双 T3 互为热备)

- **ZAI GLM Coding Plan**:单一 5h+周 共享池
  - glm-5.3(T3 旗舰,文本)
  - glm-5.3-flash(T1 快速轻档,多模态)
- **MiniMax Token Plan**:独立 5h+周 池
  - minimax-m3(T3 前沿编码/agentic,1M ctx)
  - minimax-m2.7(T2 前代)
- **OpenCode Zen 免费中继**:UNMETERED,无窗口(最后兜底)

## 结论

**可以跑通。9/9 场景通过**(真实 API)。双 T3 池结构下,池压触发的
跨池切换是"零降档热备"(ZAI 高压 → M3),置信度 1.00,决策无歧义。

## 集成形态(代码持有策略,Jev 供判断)

```
pi task ──> [代码] 硬门(窗口耗尽淘汰,共享池联动)
        ──> [代码] 压力计算(used_fraction → LOW/MODERATE/HIGH/CRITICAL)
        ──> [Jev] 1 次请求 3 问并行:
                  task_tier (Score 0–3)   任务能力分级
                  route (Choice)          在合格候选中选目标(含池压权衡)
                  defer_acceptable (Noul) 无健康替代时可否延迟
        ──> [代码] 三道策略门:
                  conf < 0.45 → tier 感知确定性兜底
                  选中目标低于 tier 下限 → 强制抬回(policy_override)
                  选中池 CRITICAL 且 defer>0.6 → 排队建议
```

## 场景结果(当前世代,最终一轮)

| 场景 | 配置 | 结果 | conf |
| --- | --- | --- | --- |
| S1 琐碎+全健康 | rename | glm-5.3-flash(轻档) | 0.89 |
| S2 复杂+全健康 | 并发 bug | glm-5.3(旗舰) | 0.82 |
| S3 复杂+ZAI 周 CRITICAL | 93% | **跨池同级** minimax-m3 | 1.00 |
| S4 复杂+ZAI 5h HIGH | 88% | **跨池同级** minimax-m3 | 1.00 |
| S5 琐碎+双付费池 CRITICAL | — | zen 免费中继 | 0.95 |
| S6 模糊任务 | — | glm-5.3-flash,分级合理 | 0.75 |
| S7 ZAI 5h 耗尽 | 硬门拦双 ZAI | minimax-m3 | 0.49 |
| S8 T3 刚性+ZAI 周 CRITICAL | 禁止降档 | minimax-m3(Jev 自己守住) | 1.00 |
| S9 杂务+双池 CRITICAL 无兜底 | — | minimax-m3 | 0.46 |

## 关键发现

1. **池级压力感知成立**:ZAI 池高压 → 不做池内降档(flash 同池),
   直接跨池切 M3(零质量损失);双池全满 → 琐碎任务让给 Zen 免费中继。
2. **双 T3 热备让决策更确定**:同样"复杂任务+ZAI 高压"从单 T3 时代
   的 conf 0.45–0.77 升到 1.00——存在无代价替代时 Jev 决策毫无歧义。
3. **刚性约束:有解时 Jev 能守住,无解时必须代码兜底**:S8(有同级
   M3 可用)Jev 自己守住 T3 pin,conf 1.00;此前无同级替代的版本里
   Jev 会摇摆(conf 0.43–0.49)→ `policy_override` 代码门仍是必需。
4. **同池多可接受选项会分票**:S7(m2.7/m3 同池皆可)conf 0.49、
   S9 conf 0.46——预期行为,兜底策略接管即可。
5. **defer 判断持续保守**(noul 0.19–0.38):Jev 倾向"窗口有余量就先做"。
   要更积极的排队行为需改问法/降阈值。
6. **池状态必须单点采集**:同池目标快照不一致(首版夹具 bug)会让
   Jev 看到自相矛盾的状态 → 置信度崩塌。生产上按池采集、按池分发。

## 成本与延迟

- 每决策:1 请求 / 3 问题并行,~1.5–1.8k in / ~120 out tokens
- 延迟 ~0.5–0.7s,对任务级调度可忽略
- 错误契约:缺 criteria → 422 字段级定位;429/529 指数退避已实现

## 生产接入建议

- 位置:hard-eligibility 之后、确定性 ranking 之前;或作为
  `dispatch_recommendation_service.py` 的 shadow 第二意见先行对照。
- state 复用 `DispatchCandidateInput.windows` 投影;池 id(ZAI/MM/Zen)
  与共享关系来自 provider registry,SKU 由 discovery 动态发现
  (tier 表用 fnmatch 匹配,换新模型只改数据)。
- 硬规则(层级下限、耗尽、冷却、池共享)全部留在代码层。
- key:服务端环境变量,不落盘。

## 实用入口:`pi-jev` 启动器(方案 A,已装)

```
pi-jev "任务"          # 决策后以选中模型打开交互式 pi
pi-jev -p "任务"       # 单次执行,输出后退出
pi-jev --dry-run "任务" # 只看调度决策
pi-jev --json "任务"    # JSON 输出,供脚本组合
```

链路:任务 → ZAI/MM 实时配额探测 → 硬门 → Jev 决策 → 以选中的
provider/model 拉起真实 `pi`。每次调度追加记录到
`results/pi_jev_log.jsonl`。已 symlink 到 `~/.local/bin/pi-jev`。
需要环境变量 `TYPESAFE_API_KEY`(自行加入 shell 配置,勿入库)。

实测(2026-09-24,真实执行):
- 琐碎任务 → glm-5.3-flash(conf 0.98),pi 真实返回结果
- T3 刚性任务 → glm-5.3(conf 0.99)
- MiniMax 无订阅 → 硬门以真实理由排除整个 MM 池

复跑:
- 决策层:`TYPESAFE_API_KEY=... python3 spikes/jev-dispatch/run_spike.py`
- 端到端:`TYPESAFE_API_KEY=... python3 spikes/jev-dispatch/run_e2e.py`
- 日常使用:`pi-jev "任务"`
