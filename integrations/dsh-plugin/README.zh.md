# PAO 的 DeepSeek Harness 插件

这个 bundle 包含两个彼此独立的 Cordis 插件：

- `pao-dsh-dispatch`：原有的 Jev 配额感知 `agent/request` 路由器；
- `pao-dsh-dispatch/telegram`：默认关闭、需显式启用的 Telegram 控制器，
  用于控制本机一个固定工作区中的 DeepSeek Harness。

Telegram 控制器只使用 Harness 公开的 `ctx.agents` 接口：创建或恢复 Agent、
提交普通用户消息、读取已提交的可见回答、执行带类型的用户取消。它不直接启动
shell，也不会绕过 Harness 的工具、沙箱或审批服务。

## 安装

```bash
dsh plugin --profile <profile-name> add github:louisjia2008-ux/personal-ai-orchestrator
```

安装后的 Telegram 配置是 `enabled: false`；仅安装不会连接 Telegram。

## 启用 Telegram 控制器

在 BotFather 创建专用 Bot，把 Token 放在环境变量中，不要写进 Git 或 YAML：

```bash
export TELEGRAM_BOT_TOKEN='...'
```

在目标 profile 的 `cordis.patch.yml` 中完整覆盖这一行（Harness patch 的配置是
整行替换，不是字段合并）：

```yaml
- id: pao-telegram-controller
  name: pao-dsh-dispatch/telegram
  config:
    enabled: true
    tokenEnv: TELEGRAM_BOT_TOKEN
    allowedUserIds: [123456789]
    allowedChatIds: [123456789]
    cwd: /absolute/path/to/the/only/allowed/workspace
    workspaceLabel: my-workspace
    pollTimeoutSeconds: 25
    ignorePendingOnFirstStart: true
```

然后重启该 profile。首次启用会跳过启用前积压的 update，避免旧消息突然执行。
控制器使用 Telegram long polling，不会擅自删除已有 webhook；如果该 Bot 已设置
webhook，`getUpdates` 会保持失败关闭，直到 Bot 所有者自行处理 webhook。

一个 Bot 的 update 队列只能由一个 long-polling 消费者持有。如果另一个 PAO
进程也在轮询 Telegram，请使用另一个 Bot Token。

模型、推理强度、输出上限、Agent preset 和状态文件均可在本机配置。省略这些项
时继承所选 Harness profile 的正常配置；插件不会猜测或偷偷替换模型 ID。

## 命令

```text
/dsh run <自然语言任务>
/dsh status
/dsh cancel
/dsh new
/dsh help
```

`/dsh new` 不接受路径。Telegram 不能修改工作目录、provider 凭据、工具策略、
沙箱策略或审批策略。普通聊天文本与未知 `/dsh` 子命令不会被当成 shell 输入。

## 安全边界

- 必须同时命中用户 ID 与聊天 ID 白名单。
- Bot Token 只从指定环境变量读取，不写入状态、输出或日志。
- 状态文件只有 update offset 与聊天到 Harness session 的映射，以 `0600` 原子写入。
- `cwd` 在本机启动时解析并固定，不能由 Telegram 指定。
- 默认跳过启用前积压的消息。
- 不向 Telegram 发送 reasoning delta 或本机错误细节；只回传已提交的可见回答，
  且任务输入、回复总量和单条消息都有上限。
- 不提供 Telegram 审批命令；Harness 的审批与沙箱仍由 Harness 本身掌握。

Telegram 仍然是远程控制面：白名单用户可以要求 Harness 使用本机 profile 已经允许
的工具，可见模型输出也会复制到 Telegram。应使用专用私有 Bot、最小权限的
Harness profile，以及你愿意被远程控制的最小工作区。

## 验证

```bash
npm --prefix integrations/dsh-plugin test
```

单元测试覆盖命令解析、双白名单、固定 cwd、带类型的取消、reasoning 隔离、
无凭据状态、Token 安全错误和 Telegram 消息长度。真实 DSH 组合与真实 Telegram
账号是独立验收门；仓库测试不会连接真实 Bot。

包加载、默认关闭的 bundle 以及 enabled 生命周期已在本机 DeepSeek Harness
`0.1.2-alpha.1` 源码版上用离线假 Bot API 完成真实组合验证。Harness 插件 API
仍属预稳定接口，升级 Harness 后应重跑该组合验收。
