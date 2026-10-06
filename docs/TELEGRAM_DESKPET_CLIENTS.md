# Telegram and DeskPet typed clients

PAO exposes Telegram and DeskPet through one typed client gateway. Both clients
observe and mutate the same durable task records through the owner-only control
socket; neither client owns worker processes or can execute a shell command.

## Security boundary

- The bridge accepts only `SUBMIT`, `STATUS`, `CANCEL`, `APPROVE`, and `REPORT`.
- Submit accepts a registered `project_id` and natural-language `intent`; there
  is no argv, command, verifier, model, provider, worktree, or shell field.
- Telegram requires both the sender user ID and chat ID to be allowlisted.
- PAO does not accept or persist a Telegram bot token. A provider-native bot
  host keeps that token in Keychain or its own secret store and passes a
  normalized, credential-free update to `pao-client-bridge`.
- Approval resolution can only resolve an approval already created by the
  Safety Kernel. It cannot create approval authority or enable Production
  ACTIVE by itself.
- The bridge talks only to the permission-restricted local Unix socket.

## Telegram bridge

This `/pao` bridge controls the PAO Safety Kernel. It is distinct from the
optional DeepSeek Harness `/dsh` Telegram controller documented in
[`integrations/dsh-plugin/README.md`](../integrations/dsh-plugin/README.md).
The `/pao` bridge deliberately has no Bot API transport or token; the `/dsh`
plugin owns its own default-off long poller and one Harness Agent boundary.
Do not run both pollers against the same Bot Token because Telegram exposes one
shared update queue per bot.

The provider-native bot host sends one normalized update on standard input:

```json
{
  "update_id": 1001,
  "message_id": 81,
  "user_id": 42,
  "chat_id": 84,
  "text": "/pao status telegram-a31d..."
}
```

```bash
pao-client-bridge \
  --socket "/path/to/control.sock" \
  telegram \
  --allow-user 42 \
  --allow-chat 84
```

Supported commands:

```text
/pao submit <registered-project-id> <natural-language intent>
/pao status <task-id>
/pao cancel <task-id>
/pao approve <approval-id>
/pao reject <approval-id>
/pao report <task-id>
```

The stable request ID is derived from `(chat_id, message_id)`. Replaying the
same Telegram update therefore reaches the control plane with the same
idempotency key and cannot create a duplicate task.

## DeskPet tool bridge

DeskPet sends a strict `DeskPetToolRequest` JSON object:

```json
{
  "request_id": "deskpet-message-81",
  "operation": "STATUS",
  "task_id": "telegram-a31d..."
}
```

```bash
pao-client-bridge \
  --socket "/path/to/control.sock" \
  deskpet \
  --installation-id "deskpet-local"
```

The response is the same sanitized typed view returned to Telegram. A DeskPet
or Telegram process crash does not affect a worker: worker lifetime and task
state remain owned by the daemon/Safety Kernel.

## Proactive task-state notifications

Run the watcher as a long-lived sidecar and route each JSON line to Telegram or
DeskPet using that client's native notification channel:

```bash
pao-client-bridge --socket "/path/to/control.sock" watch --interval 2
```

The watcher establishes a baseline without replaying historical tasks, then
emits only newly observed tasks and durable task-state transitions. It never
changes task state and may be restarted independently of workers.

## Test contract

`tests/test_client_gateway.py` proves:

- duplicate Telegram updates map to one durable task;
- Telegram and DeskPet see identical task state;
- a non-allowlisted sender cannot submit or mutate anything;
- deleting/crashing a client adapter leaves a RUNNING task intact;
- approval is a fixed typed operation;
- unexpected `argv`/shell fields are rejected;
- proactive notifications emit only new or changed durable state.

## Opt-in manual execution v2

The separate [local-client v2 contract](LOCAL_CLIENT_V2.md) adds explicit
registered-target MANUAL submission, owner START, bounded read-only DETAIL,
original-tuple dispatch reconciliation and manual task cancellation. It binds
mutations to a durable store identity and the current daemon process epoch.
Legacy requests without `protocol_version` keep the contract above; v2 does not
accept approval decisions or enable any autonomous execution mode.
