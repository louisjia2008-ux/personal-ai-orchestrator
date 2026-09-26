# pi-dsh — Jev 配额感知模型调度插件

pi 扩展包:把 [Personal AI Orchestrator](../../README.md) 的调度决策内核
(Jev,TypeSafe System One)装进 pi——每轮请求前实时探测三个模型池的
配额/余额,硬门过滤后由 Jev 决策,自动把会话切到最优模型。

## 池与经济规则

| 池 | 模型(pi 目录) | 计费 | 参与条件(实时) |
| --- | --- | --- | --- |
| ZAI Coding Plan | glm-5.3 / glm-5.3-flash | 订阅 | 5h/周窗口压力 |
| MiniMax Token Plan | MiniMax-M3 / M2.7 | 订阅 | 订阅有效 |
| DeepSeek | deepseek-v4-pro / v4-flash | paygo | 余额 ≥1 元;<10 元只留 flash |

订阅池健康时优先(已付费);高压时跨池 failover;琐事走轻档,不烧真金白银。

## 安装

```bash
pi install /path/to/personal-ai-orchestrator/integrations/pi-dsh
# 或从 git:
pi install git:github.com/louisjia2008-ux/personal-ai-orchestrator
```

需要环境变量 `TYPESAFE_API_KEY`(Jev 决策层);模型凭证用 pi 自己的
auth store(`pi auth check`)。

## 使用

```
/dsh              # 查看状态
/dsh on           # 开启每轮自动调度(默认关)
/dsh off          # 关闭
/dsh pick <任务>   # 对任意文本做一次调度决策预览
```

- 自动调度开启后:每次提交 prompt → 探测(约 1–2s)→ 决策 → 需要时
  `pi.setModel()` 切换,并给出通知(`模型A → 模型B (conf, tier)`)
- 决策失败永不阻塞任务:异常时通知并沿用当前模型
- `DSH_AUTO=1` 启动即开启;`DSH_DEBUG=1` 输出 stderr 调试日志

## 架构(代码管规则,Jev 管语义)

```
prompt → 实时探测(ZAI quota / MM token_plan / DS balance)
      → 硬门(耗尽/无订阅/余额不足淘汰)
      → Jev 一次三问(task_tier 分级 / route 选路 / defer 可否延迟)
      → 代码策略门(conf<0.45 兜底排序;低于 tier 下限强制抬回)
      → pi.setModel(选中模型)
```

调度记录:`spikes/jev-dispatch/results/`(决策层验证与端到端数据见
`spikes/jev-dispatch/results/JEV_DISPATCH_SPIKE.md`)。
