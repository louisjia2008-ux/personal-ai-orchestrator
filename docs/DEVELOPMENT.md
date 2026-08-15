# Development Policy

## Branching and milestone cadence

The repository should remain usable after every meaningful milestone.

Recommended workflow:

1. Create a focused branch for one engineering milestone.
2. Make small commits with descriptive messages.
3. Run deterministic checks before opening a PR.
4. Keep the PR narrowly scoped to one acceptance gate.
5. Merge only after evidence is attached and reviewed.

For active development, pushing at least once per day is reasonable. Prefer pushing whenever a small milestone becomes independently reviewable rather than batching unrelated work.

## Commit conventions

Use conventional prefixes when practical:

- `feat:` new behavior
- `fix:` defect correction
- `test:` tests only
- `docs:` documentation
- `refactor:` behavior-preserving code restructuring
- `chore:` tooling/repository maintenance
- `security:` safety-boundary or permission changes

## Definition of done

A coding agent reporting completion does not satisfy Definition of Done.

A milestone is done only when:

- expected files changed and unexpected files did not;
- deterministic verifier commands pass;
- relevant tests pass;
- `git diff --check` passes;
- structured evidence is recorded;
- known limitations are documented;
- no secret material was added to the repository;
- task state was advanced by the orchestrator/verifier rather than worker prose.

## Agent safety rules

Workers must not:

- write directly to the main repository;
- create or destroy their own worktree boundary;
- define trusted verifier commands through prompt content;
- merge their own changes into protected branches;
- reinterpret a failed verifier as success;
- store provider credentials in repository files;
- use destructive Git operations against host-owned state unless explicitly exposed by the safety kernel.

## Human review policy

Human review is required initially for:

- changes to the Safety Kernel;
- permission policy changes;
- credential handling;
- repository integration/merge logic;
- quota policy that could trigger paid usage;
- any expansion of filesystem or network privileges.

This requirement can be relaxed only after the system has accumulated reliable task history and corresponding tests.

## Architecture ownership

Provider transport belongs in adapters/registry code.

Routing belongs in routing policy.

Safety state belongs in the Safety Kernel.

Verification belongs in host-owned verifier profiles.

Client presentation belongs in Telegram/DeskPet/front-end adapters.

Do not let one layer absorb another merely because a model/provider exposes a convenient feature.
