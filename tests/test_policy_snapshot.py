from personal_ai_orchestrator.policy_snapshot import PolicySnapshot, PolicySnapshotJournal
from personal_ai_orchestrator.scheduler import RoutingObjective, RoutingPolicy


def test_policy_snapshot_identity_is_content_addressed() -> None:
    first = PolicySnapshot.from_policy(RoutingPolicy())
    second = PolicySnapshot.from_policy(RoutingPolicy())
    changed = PolicySnapshot.from_policy(RoutingPolicy(objective=RoutingObjective.MAX_QUALITY))
    assert first.id == second.id
    assert first.id != changed.id


def test_policy_snapshot_journal_is_append_only(tmp_path) -> None:
    snapshot = PolicySnapshot.from_policy(RoutingPolicy())
    journal = PolicySnapshotJournal(tmp_path)
    first = journal.append(snapshot)
    second = journal.append(snapshot)
    assert first == second
    assert journal.load(snapshot.id) == snapshot
