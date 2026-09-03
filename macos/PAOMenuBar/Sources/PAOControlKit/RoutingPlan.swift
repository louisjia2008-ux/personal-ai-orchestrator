import Foundation

// Routing plan / role / decision contract. See docs/ROUTING_ROLE_CONTRACT.md.
//
// A plan is not a decision. A plan declares which roles an execution requires;
// each role carries its own lifecycle status, its own outcome, and its own
// independent history of selections. A role that was rerouted twice is a
// different fact from a role that was chosen once, and the model keeps them
// distinguishable.
//
// `declared_roles` is authoritative orchestrator policy state. Nothing in this
// file may synthesize a role, with exactly one documented exception: a daemon
// predating the contract reports a single legacy decision, and the legacy
// adapter turns it into the PRIMARY role that shape already implies. That
// projection is flagged so the UI never claims plan facts it does not have.

// MARK: - Role

/// A routing role. Unrecognized values are preserved verbatim rather than
/// dropped: an unknown role still describes a real assignment the owner must be
/// able to see.
public enum RoutingRole: Equatable, Hashable, Sendable {
    case primary
    case reviewer
    case finalAuditor
    case other(String)

    public init(rawValue: String) {
        switch rawValue {
        case "PRIMARY": self = .primary
        case "REVIEWER": self = .reviewer
        case "FINAL_AUDITOR": self = .finalAuditor
        default: self = .other(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .primary: return "PRIMARY"
        case .reviewer: return "REVIEWER"
        case .finalAuditor: return "FINAL_AUDITOR"
        case .other(let value): return value
        }
    }

    /// False for roles this build does not know. Such roles still render; the
    /// flag only tells the view to fall back to the verbatim machine value
    /// instead of a localized label.
    public var isKnown: Bool {
        if case .other = self { return false }
        return true
    }

    /// Canonical presentation order. Roles outside this list keep the order the
    /// daemon declared them in, after the known ones.
    public static let canonicalOrder: [RoutingRole] = [.primary, .reviewer, .finalAuditor]
}

// MARK: - Status and outcome

/// Lifecycle position of a role. Deliberately separate from `RoutingOutcome`:
/// collapsing them would make "completed and rejected the work" indistinguishable
/// from "failed to run".
public enum RoutingRoleStatus: Equatable, Hashable, Sendable {
    case unassigned
    case assigned
    case running
    case completed
    case cancelled
    case unknown(String)

    public init(rawValue: String) {
        switch rawValue {
        case "UNASSIGNED": self = .unassigned
        case "ASSIGNED": self = .assigned
        case "RUNNING": self = .running
        case "COMPLETED": self = .completed
        case "CANCELLED": self = .cancelled
        default: self = .unknown(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .unassigned: return "UNASSIGNED"
        case .assigned: return "ASSIGNED"
        case .running: return "RUNNING"
        case .completed: return "COMPLETED"
        case .cancelled: return "CANCELLED"
        case .unknown(let value): return value
        }
    }

    /// An unrecognized status is never presented as settled work.
    public var isKnown: Bool {
        if case .unknown = self { return false }
        return true
    }

    public var isTerminal: Bool {
        self == .completed || self == .cancelled
    }
}

/// Execution result of a role. `none` is the only valid outcome before a role
/// reaches a terminal status.
public enum RoutingOutcome: Equatable, Hashable, Sendable {
    case none
    case pass
    case fail
    case error
    case unknown(String)

    public init(rawValue: String) {
        switch rawValue {
        case "NONE": self = .none
        case "PASS": self = .pass
        case "FAIL": self = .fail
        case "ERROR": self = .error
        default: self = .unknown(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .none: return "NONE"
        case .pass: return "PASS"
        case .fail: return "FAIL"
        case .error: return "ERROR"
        case .unknown(let value): return value
        }
    }

    public var isKnown: Bool {
        if case .unknown = self { return false }
        return true
    }
}

// MARK: - Wire models

/// One candidate the scheduler considered for a decision. Structured scheduler
/// evidence, never model-generated prose.
public struct RoutingPlanCandidateView: Codable, Equatable, Identifiable, Sendable {
    public let executionTargetId: String?
    public let providerId: String?
    public let modelDisplayName: String?
    public let modelSkuId: String?
    public let score: Double?
    public let eligible: Bool?
    public let admitted: Bool?
    public let selected: Bool?
    public let whyNotSelected: String?
    public let quotaSnapshotId: String?

    public var id: String {
        executionTargetId ?? modelSkuId ?? modelDisplayName ?? UUID().uuidString
    }

    enum CodingKeys: String, CodingKey {
        case executionTargetId = "execution_target_id"
        case providerId = "provider_id"
        case modelDisplayName = "model_display_name"
        case modelSkuId = "model_sku_id"
        case score
        case eligible
        case admitted
        case selected
        case whyNotSelected = "why_not_selected"
        case quotaSnapshotId = "quota_snapshot_id"
    }

    public init(
        executionTargetId: String? = nil,
        providerId: String? = nil,
        modelDisplayName: String? = nil,
        modelSkuId: String? = nil,
        score: Double? = nil,
        eligible: Bool? = nil,
        admitted: Bool? = nil,
        selected: Bool? = nil,
        whyNotSelected: String? = nil,
        quotaSnapshotId: String? = nil
    ) {
        self.executionTargetId = executionTargetId
        self.providerId = providerId
        self.modelDisplayName = modelDisplayName
        self.modelSkuId = modelSkuId
        self.score = score
        self.eligible = eligible
        self.admitted = admitted
        self.selected = selected
        self.whyNotSelected = whyNotSelected
        self.quotaSnapshotId = quotaSnapshotId
    }
}

/// One selection made for one role at one moment. Independent of every other
/// decision, including other decisions for the same role.
public struct RoutingDecisionRecordView: Codable, Equatable, Identifiable, Sendable {
    public let decisionId: String
    public let roleRaw: String
    public let createdAt: String
    public let mode: String?
    public let selectedExecutionTargetId: String?
    public let providerId: String?
    public let modelDisplayName: String?
    public let whySelected: String?
    public let supersedesDecisionId: String?
    public let rerouteReason: String?
    public let quotaSnapshotId: String?
    public let candidates: [RoutingPlanCandidateView]

    public var id: String { decisionId }
    public var role: RoutingRole { RoutingRole(rawValue: roleRaw) }

    /// True when this decision replaced an earlier one for the same role.
    public var isReroute: Bool { supersedesDecisionId != nil }

    enum CodingKeys: String, CodingKey {
        case decisionId = "decision_id"
        case roleRaw = "role"
        case createdAt = "created_at"
        case mode
        case selectedExecutionTargetId = "selected_execution_target_id"
        case providerId = "provider_id"
        case modelDisplayName = "model_display_name"
        case whySelected = "why_selected"
        case supersedesDecisionId = "supersedes_decision_id"
        case rerouteReason = "reroute_reason"
        case quotaSnapshotId = "quota_snapshot_id"
        case candidates
    }

    public init(
        decisionId: String,
        role: RoutingRole,
        createdAt: String,
        mode: String? = nil,
        selectedExecutionTargetId: String? = nil,
        providerId: String? = nil,
        modelDisplayName: String? = nil,
        whySelected: String? = nil,
        supersedesDecisionId: String? = nil,
        rerouteReason: String? = nil,
        quotaSnapshotId: String? = nil,
        candidates: [RoutingPlanCandidateView] = []
    ) {
        self.decisionId = decisionId
        self.roleRaw = role.rawValue
        self.createdAt = createdAt
        self.mode = mode
        self.selectedExecutionTargetId = selectedExecutionTargetId
        self.providerId = providerId
        self.modelDisplayName = modelDisplayName
        self.whySelected = whySelected
        self.supersedesDecisionId = supersedesDecisionId
        self.rerouteReason = rerouteReason
        self.quotaSnapshotId = quotaSnapshotId
        self.candidates = candidates
    }

    /// Tolerant decoding: a daemon that omits optional bookkeeping still yields a
    /// usable decision rather than failing the whole task detail.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        decisionId = try container.decode(String.self, forKey: .decisionId)
        roleRaw = try container.decode(String.self, forKey: .roleRaw)
        createdAt = try container.decode(String.self, forKey: .createdAt)
        mode = try container.decodeIfPresent(String.self, forKey: .mode)
        selectedExecutionTargetId = try container.decodeIfPresent(
            String.self, forKey: .selectedExecutionTargetId
        )
        providerId = try container.decodeIfPresent(String.self, forKey: .providerId)
        modelDisplayName = try container.decodeIfPresent(String.self, forKey: .modelDisplayName)
        whySelected = try container.decodeIfPresent(String.self, forKey: .whySelected)
        supersedesDecisionId = try container.decodeIfPresent(
            String.self, forKey: .supersedesDecisionId
        )
        rerouteReason = try container.decodeIfPresent(String.self, forKey: .rerouteReason)
        quotaSnapshotId = try container.decodeIfPresent(String.self, forKey: .quotaSnapshotId)
        candidates =
            try container.decodeIfPresent([RoutingPlanCandidateView].self, forKey: .candidates)
            ?? []
    }
}

/// Where one declared role currently stands, plus every selection made for it.
public struct RoutingRoleStateView: Codable, Equatable, Identifiable, Sendable {
    public let roleRaw: String
    public let statusRaw: String
    public let outcomeRaw: String
    public let activeDecisionId: String?
    public let decisions: [RoutingDecisionRecordView]

    public var id: String { roleRaw }
    public var role: RoutingRole { RoutingRole(rawValue: roleRaw) }
    public var status: RoutingRoleStatus { RoutingRoleStatus(rawValue: statusRaw) }
    public var outcome: RoutingOutcome { RoutingOutcome(rawValue: outcomeRaw) }

    enum CodingKeys: String, CodingKey {
        case roleRaw = "role"
        case statusRaw = "status"
        case outcomeRaw = "outcome"
        case activeDecisionId = "active_decision_id"
        case decisions
    }

    public init(
        role: RoutingRole,
        status: RoutingRoleStatus,
        outcome: RoutingOutcome = .none,
        activeDecisionId: String? = nil,
        decisions: [RoutingDecisionRecordView] = []
    ) {
        self.roleRaw = role.rawValue
        self.statusRaw = status.rawValue
        self.outcomeRaw = outcome.rawValue
        self.activeDecisionId = activeDecisionId
        self.decisions = decisions
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        roleRaw = try container.decode(String.self, forKey: .roleRaw)
        statusRaw = try container.decode(String.self, forKey: .statusRaw)
        // A daemon that reports lifecycle but not result is treated as "no result
        // yet" rather than as a missing field, so the pair never renders empty.
        outcomeRaw = try container.decodeIfPresent(String.self, forKey: .outcomeRaw) ?? "NONE"
        activeDecisionId = try container.decodeIfPresent(String.self, forKey: .activeDecisionId)
        decisions =
            try container.decodeIfPresent([RoutingDecisionRecordView].self, forKey: .decisions)
            ?? []
    }
}

/// One execution plan revision for a task.
///
/// Plans are immutable. A policy or escalation change produces a new revision
/// and leaves the previous one intact, so routing history stays auditable.
public struct RoutingPlanView: Codable, Equatable, Identifiable, Sendable {
    public let planId: String
    public let revision: Int
    public let taskId: String
    public let createdAt: String
    public let policyId: String?
    public let resolvedPolicy: String?
    public let policyResolutionSource: String?
    public let supersededByPlanId: String?
    /// Authoritative. Never inferred, extended, or filtered by the client.
    public let declaredRoles: [String]
    public let roles: [RoutingRoleStateView]

    public var id: String { "\(planId)#\(revision)" }

    /// True when a newer revision replaced this plan.
    public var isSuperseded: Bool { supersededByPlanId != nil }

    enum CodingKeys: String, CodingKey {
        case planId = "plan_id"
        case revision
        case taskId = "task_id"
        case createdAt = "created_at"
        case policyId = "policy_id"
        case resolvedPolicy = "resolved_policy"
        case policyResolutionSource = "policy_resolution_source"
        case supersededByPlanId = "superseded_by_plan_id"
        case declaredRoles = "declared_roles"
        case roles
    }

    public init(
        planId: String,
        revision: Int,
        taskId: String,
        createdAt: String,
        policyId: String? = nil,
        resolvedPolicy: String? = nil,
        policyResolutionSource: String? = nil,
        supersededByPlanId: String? = nil,
        declaredRoles: [String],
        roles: [RoutingRoleStateView]
    ) {
        self.planId = planId
        self.revision = revision
        self.taskId = taskId
        self.createdAt = createdAt
        self.policyId = policyId
        self.resolvedPolicy = resolvedPolicy
        self.policyResolutionSource = policyResolutionSource
        self.supersededByPlanId = supersededByPlanId
        self.declaredRoles = declaredRoles
        self.roles = roles
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        planId = try container.decode(String.self, forKey: .planId)
        revision = try container.decodeIfPresent(Int.self, forKey: .revision) ?? 1
        taskId = try container.decode(String.self, forKey: .taskId)
        createdAt = try container.decode(String.self, forKey: .createdAt)
        policyId = try container.decodeIfPresent(String.self, forKey: .policyId)
        resolvedPolicy = try container.decodeIfPresent(String.self, forKey: .resolvedPolicy)
        policyResolutionSource = try container.decodeIfPresent(
            String.self, forKey: .policyResolutionSource
        )
        supersededByPlanId = try container.decodeIfPresent(
            String.self, forKey: .supersededByPlanId
        )
        declaredRoles = try container.decodeIfPresent([String].self, forKey: .declaredRoles) ?? []
        roles = try container.decodeIfPresent([RoutingRoleStateView].self, forKey: .roles) ?? []
    }
}

// MARK: - Normalized UI projection

/// What one declared role looks like to a view.
///
/// Built by `TaskRoutingSummary`; views never assemble this themselves.
public struct RoutingRoleSummary: Equatable, Identifiable, Sendable {
    public let role: RoutingRole
    public let status: RoutingRoleStatus
    public let outcome: RoutingOutcome
    /// The selection currently in force, if any.
    public let activeDecision: RoutingDecisionRecordView?
    /// Every decision made for this role, newest first.
    public let history: [RoutingDecisionRecordView]

    public var id: String { role.rawValue }

    /// Declared by the plan but not yet assigned. This is an explicit state, not
    /// an absence: a role that the plan never declared produces no summary at all.
    public var isPending: Bool { activeDecision == nil }

    /// How many times this role was re-decided after its first selection.
    public var rerouteCount: Int { max(0, history.count - 1) }

    public init(
        role: RoutingRole,
        status: RoutingRoleStatus,
        outcome: RoutingOutcome,
        activeDecision: RoutingDecisionRecordView?,
        history: [RoutingDecisionRecordView]
    ) {
        self.role = role
        self.status = status
        self.outcome = outcome
        self.activeDecision = activeDecision
        self.history = history
    }
}

/// Normalized, read-only routing projection for Task Detail.
///
/// Views consume this instead of raw plan/decision bookkeeping, so routing
/// semantics live in one tested place rather than being reconstructed per view.
public struct TaskRoutingSummary: Equatable, Sendable {
    public let planId: String?
    public let planRevision: Int?
    /// True when this summary was reconstructed from a pre-contract daemon's
    /// single decision. The UI must not present plan identity or revision as
    /// authoritative facts in that case.
    public let isLegacySynthesized: Bool
    public let isSuperseded: Bool
    public let policyId: String?
    public let resolvedPolicy: String?
    public let policyResolutionSource: String?
    /// Exactly the roles the plan declared, in canonical order. Never widened,
    /// never narrowed.
    public let roles: [RoutingRoleSummary]

    public init(
        planId: String?,
        planRevision: Int?,
        isLegacySynthesized: Bool,
        isSuperseded: Bool,
        policyId: String?,
        resolvedPolicy: String?,
        policyResolutionSource: String?,
        roles: [RoutingRoleSummary]
    ) {
        self.planId = planId
        self.planRevision = planRevision
        self.isLegacySynthesized = isLegacySynthesized
        self.isSuperseded = isSuperseded
        self.policyId = policyId
        self.resolvedPolicy = resolvedPolicy
        self.policyResolutionSource = policyResolutionSource
        self.roles = roles
    }

    public func role(_ role: RoutingRole) -> RoutingRoleSummary? {
        roles.first { $0.role == role }
    }

    public var primary: RoutingRoleSummary? { role(.primary) }

    /// Whether the plan requires this role at all. Distinct from "assigned":
    /// a declared-but-unassigned role is declared.
    public func declares(_ role: RoutingRole) -> Bool {
        roles.contains { $0.role == role }
    }
}

// MARK: - Projection construction

extension TaskRoutingSummary {
    /// Builds the projection from whatever the daemon supplied.
    ///
    /// Precedence is deliberate: an authoritative plan always wins. The legacy
    /// decision is only consulted when no plan exists at all, and it yields the
    /// single PRIMARY role that the legacy shape already implies — the one and
    /// only role construction permitted anywhere in the client.
    public static func make(
        plan: RoutingPlanView?,
        legacyDecision: RoutingDecisionView?
    ) -> TaskRoutingSummary? {
        if let plan {
            return fromPlan(plan)
        }
        if let legacyDecision {
            return fromLegacy(legacyDecision)
        }
        return nil
    }

    private static func fromPlan(_ plan: RoutingPlanView) -> TaskRoutingSummary {
        let statesByRole = Dictionary(
            plan.roles.map { (RoutingRole(rawValue: $0.roleRaw), $0) },
            uniquingKeysWith: { first, _ in first }
        )

        // `declared_roles` is authoritative, so it — not `roles` — drives what the
        // UI shows. A declared role missing its state entry is still declared, and
        // renders as unassigned rather than silently disappearing.
        let declared = plan.declaredRoles.map(RoutingRole.init(rawValue:))
        let ordered = orderRoles(declared)

        let summaries = ordered.map { role -> RoutingRoleSummary in
            guard let state = statesByRole[role] else {
                return RoutingRoleSummary(
                    role: role,
                    status: .unassigned,
                    outcome: .none,
                    activeDecision: nil,
                    history: []
                )
            }
            // Wire order is oldest-first; the projection hands views newest-first.
            let history = Array(state.decisions.reversed())
            let active =
                state.activeDecisionId.flatMap { id in
                    state.decisions.first { $0.decisionId == id }
                } ?? history.first
            return RoutingRoleSummary(
                role: role,
                status: state.status,
                outcome: state.outcome,
                activeDecision: active,
                history: history
            )
        }

        return TaskRoutingSummary(
            planId: plan.planId,
            planRevision: plan.revision,
            isLegacySynthesized: false,
            isSuperseded: plan.isSuperseded,
            policyId: plan.policyId,
            resolvedPolicy: plan.resolvedPolicy,
            policyResolutionSource: plan.policyResolutionSource,
            roles: summaries
        )
    }

    private static func fromLegacy(_ decision: RoutingDecisionView) -> TaskRoutingSummary {
        let candidates = decision.candidates.map { raw -> RoutingPlanCandidateView in
            RoutingPlanCandidateView(
                executionTargetId: raw["execution_target_id"]?.value,
                providerId: raw["provider_id"]?.value,
                modelDisplayName: raw["model_display_name"]?.value,
                modelSkuId: raw["model_sku_id"]?.value,
                score: raw["score"]?.doubleValue,
                eligible: raw["eligible"]?.boolValue,
                admitted: raw["admitted"]?.boolValue,
                selected: raw["selected"]?.boolValue,
                whyNotSelected: raw["why_not_selected"]?.value,
                quotaSnapshotId: raw["quota_snapshot_id"]?.value
            )
        }

        let chosen: RoutingPlanCandidateView? = candidates.first { $0.selected == true }
        let selectedTarget: String? = decision.selectedExecutionTargetId

        let record = RoutingDecisionRecordView(
            decisionId: decision.decisionId,
            role: .primary,
            createdAt: decision.createdAt,
            mode: decision.mode,
            selectedExecutionTargetId: selectedTarget,
            providerId: chosen?.providerId,
            modelDisplayName: chosen?.modelDisplayName,
            whySelected: decision.whySelected,
            quotaSnapshotId: chosen?.quotaSnapshotId,
            candidates: candidates
        )

        // A legacy decision reports a selection, not a lifecycle. Claiming
        // COMPLETED here would invent a fact; ASSIGNED is what the shape supports.
        let hasSelection: Bool = selectedTarget != nil
        let status: RoutingRoleStatus = hasSelection ? .assigned : .unassigned
        let history: [RoutingDecisionRecordView] = hasSelection ? [record] : []

        let summary = RoutingRoleSummary(
            role: .primary,
            status: status,
            outcome: .none,
            activeDecision: hasSelection ? record : nil,
            history: history
        )

        return TaskRoutingSummary(
            planId: nil,
            planRevision: nil,
            isLegacySynthesized: true,
            isSuperseded: false,
            policyId: decision.policyId,
            resolvedPolicy: decision.resolvedPolicy,
            policyResolutionSource: decision.policyResolutionSource,
            roles: [summary]
        )
    }

    /// Known roles first in canonical order, then any unrecognized roles in the
    /// order the daemon declared them. Unknown roles are kept, never dropped.
    private static func orderRoles(_ declared: [RoutingRole]) -> [RoutingRole] {
        var seen = Set<RoutingRole>()
        var known: [RoutingRole] = []
        var unknown: [RoutingRole] = []
        for role in declared where !seen.contains(role) {
            seen.insert(role)
            if RoutingRole.canonicalOrder.contains(role) {
                known.append(role)
            } else {
                unknown.append(role)
            }
        }
        known.sort { lhs, rhs in
            let l = RoutingRole.canonicalOrder.firstIndex(of: lhs) ?? Int.max
            let r = RoutingRole.canonicalOrder.firstIndex(of: rhs) ?? Int.max
            return l < r
        }
        return known + unknown
    }
}
