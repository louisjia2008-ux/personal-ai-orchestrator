# Security Policy

## Supported versions

Personal AI Orchestrator is pre-alpha. Security fixes are applied to the latest
`main` branch only; no released version currently receives backports.

## Reporting a vulnerability

Please use GitHub's private **Report a vulnerability** / Security Advisory
flow for this repository. Do not open a public issue for an unpatched secret
exposure, command-execution path, authentication bypass, worktree escape,
approval bypass, paid-usage bypass, or unsafe process-ownership defect.

Include:

- affected commit and platform;
- the smallest reproducible case;
- impact and the authority boundary crossed;
- whether a credential or real provider account was involved;
- any temporary mitigation already applied.

Do not include live credentials, auth-store contents, access tokens, private
keys, personal filesystem paths, or raw provider transcripts. Use a disposable
canary when proof requires secret-shaped input.

The maintainer will acknowledge a report as soon as practical, validate it
against the current branch, and coordinate a fix and disclosure. Because this
is a volunteer pre-alpha project, no fixed response-time SLA is promised.

## Security invariants

- Natural language cannot define executable verifier or shell commands.
- Worker prose cannot mark a task verified or complete.
- Unknown identity, quota, process, or recovery state fails closed.
- Provider credentials remain in approved native stores or narrow in-memory
  request scope and never enter Git, SQLite, evidence, logs, or API views.
- Client adapters cannot create approval authority or enable Production ACTIVE.
- Process termination targets an exact host-owned identity; broad process-name
  termination is not an ownership mechanism.
- Automatic paid overage requires explicit owner policy.
