# Z.AI Quota Observation

Status: `DEFERRED_PENDING_QUOTA_RESET_OR_AUTH`

As of: 2026-08-29T00:58:00Z

Official provider documentation and the provider-published Coding Plan usage plugin were audited. The provider publishes remote usage/quota surfaces covering 5-hour, weekly, and monthly MCP/tool quota concepts.

The P3 adapter normalizes mocked provider responses only in this phase. No additional Z.AI model calls are made, and no attempt is made to read/copy the OpenCode credential store.

- official quota endpoint recorded: `https://api.z.ai/api/monitor/usage/quota/limit`
- runtime status: `DEFERRED_PENDING_QUOTA_RESET_OR_AUTH`
- current remaining: `UNKNOWN`
- current reset: `UNKNOWN`
- runtime confidence: `UNKNOWN`
- credentials read: NO
- credentials logged: NO
- completion quota consumed: NO

The adapter marks a remaining fraction derived from the provider plugin's usage percentage as `ESTIMATED`, never `EXACT`.
