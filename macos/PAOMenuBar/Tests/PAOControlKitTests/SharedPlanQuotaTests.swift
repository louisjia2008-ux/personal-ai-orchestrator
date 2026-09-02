import Foundation

import XCTest

@testable import PAOControlKit

/// P4.2.6.5 — the Quota page leads with the subscription plan.
///
/// These tests pin the client-side half of the shared-pool contract: the plan
/// projection decodes, the three kinds of fact stay separately addressable, and
/// a derived estimate can never present itself as a provider balance.
final class SharedPlanQuotaTests: XCTestCase {

    private func decodeCard(_ json: String) throws -> QuotaProviderCardView {
        try JSONDecoder().decode(QuotaProviderCardView.self, from: Data(json.utf8))
    }

    // MARK: - Shared pool decoding

    /// The exact GLM shape the daemon emits from a live read.
    private let glmCard = """
        {
          "provider_id": "zai-coding-plan",
          "display_name": "GLM / Z.AI",
          "connection_state": "CONNECTED",
          "quota_state": "OBSERVED",
          "confidence": "EXACT",
          "credential_source": "OPENCODE_AUTH_STORE",
          "quota_pools": [],
          "plan": {
            "provider_id": "zai",
            "plan_id": "coding-plan",
            "display_name": "GLM Coding Plan",
            "plan_level": "lite",
            "quota_semantics": "SHARED_POOL",
            "pool_id": "zai-coding-plan",
            "resource_kind": "CODING_PLAN_USAGE_POOL",
            "shared_across_models": true,
            "unit_kind": "PLAN_CREDITS",
            "covered_model_ids": ["GLM-5.3", "GLM-5.3-Flash"],
            "state": "AVAILABLE",
            "confidence": "EXACT",
            "observed_at": "2026-09-02T12:00:00+00:00",
            "unknown_reason": null,
            "windows": [
              {"window_id": "5h", "window_kind": "FIVE_HOUR", "state": "AVAILABLE",
               "confidence": "EXACT", "remaining_fraction": 1.0,
               "remaining_units": 2000, "total_units": 2000, "unit": "plan_credits",
               "reset_at": null},
              {"window_id": "weekly", "window_kind": "WEEKLY", "state": "AVAILABLE",
               "confidence": "EXACT", "remaining_fraction": 0.1612,
               "remaining_units": 1612, "total_units": 10000, "unit": "plan_credits",
               "reset_at": "2026-09-06T05:08:04+00:00"}
            ],
            "binding_window": {
              "window_id": "weekly", "window_kind": "WEEKLY",
              "remaining_fraction": 0.1612,
              "reset_at": "2026-09-06T05:08:04+00:00",
              "seconds_until_reset": 320884.0,
              "reason": "SCARCEST_COMPARABLE_WINDOW", "confidence": "EXACT"
            },
            "model_consumption": [
              {"model_id": "GLM-5.3", "consumed_units": 16835950,
               "unit_kind": "TOKENS", "provider_unit_label": "tokensUsage",
               "confidence": "EXACT", "measurement_source": "PROVIDER_USAGE_API"}
            ],
            "model_equivalents": [],
            "equivalent_capacity": [
              {"model_id": "GLM-5.3", "window_id": "weekly", "task_class": "default",
               "estimated_remaining_tasks": null, "sample_count": 0,
               "small_sample": false, "confidence": "ESTIMATED",
               "unavailable_reason": "INSUFFICIENT_SAMPLE"}
            ]
          }
        }
        """

    func testSharedPoolCarriesBothWindowsSeparately() throws {
        let plan = try XCTUnwrap(decodeCard(glmCard).plan)

        XCTAssertEqual(plan.windows.count, 2)
        let fiveHour = try XCTUnwrap(plan.windows.first { $0.windowKind == "FIVE_HOUR" })
        let weekly = try XCTUnwrap(plan.windows.first { $0.windowKind == "WEEKLY" })
        XCTAssertEqual(fiveHour.remainingFraction, 1.0)
        XCTAssertEqual(weekly.remainingFraction ?? 0, 0.1612, accuracy: 0.0001)
        // Distinct capacities prove they are not two views of one window.
        XCTAssertNotEqual(fiveHour.totalUnits, weekly.totalUnits)
    }

    func testProviderProductNameIsPreservedVerbatim() throws {
        let plan = try XCTUnwrap(decodeCard(glmCard).plan)

        // Not renamed to "GLM Token Plan" for symmetry with MiniMax: the owner
        // reconciles this card against the Z.AI console.
        XCTAssertEqual(plan.displayName, "GLM Coding Plan")
        XCTAssertEqual(plan.planLevel, "lite")
    }

    func testModelsAreListedAsPoolMembersNotAsBalances() throws {
        let plan = try XCTUnwrap(decodeCard(glmCard).plan)

        XCTAssertTrue(plan.sharedAcrossModels)
        XCTAssertEqual(plan.quotaSemantics, "SHARED_POOL")
        XCTAssertEqual(plan.coveredModelIds, ["GLM-5.3", "GLM-5.3-Flash"])
        // Membership is a list of names. There is no per-model window anywhere
        // in the payload for the view to draw a bar from.
        XCTAssertEqual(plan.windows.count, 2)
    }

    func testModelConsumptionIsNotAModelBalance() throws {
        let plan = try XCTUnwrap(decodeCard(glmCard).plan)
        let usage = try XCTUnwrap(plan.modelConsumption.first)

        XCTAssertEqual(usage.modelId, "GLM-5.3")
        XCTAssertEqual(usage.consumedUnits, 16_835_950)
        // Consumption is metered in tokens while the pool is metered in
        // credits, so the two can never be silently divided into each other.
        XCTAssertEqual(usage.unitKind, "TOKENS")
        XCTAssertEqual(plan.unitKind, "PLAN_CREDITS")
    }

    func testBindingWindowSeparatesScarcityFromResetHorizon() throws {
        let binding = try XCTUnwrap(decodeCard(glmCard).plan?.bindingWindow)

        XCTAssertEqual(binding.windowId, "weekly")
        XCTAssertEqual(binding.reason, "SCARCEST_COMPARABLE_WINDOW")
        // Both facts survive as separate fields rather than one blended score.
        XCTAssertNotNil(binding.remainingFraction)
        XCTAssertNotNil(binding.secondsUntilReset)
    }

    // MARK: - Estimated capacity

    func testMissingEstimateIsAbsentRatherThanZero() throws {
        let capacity = try XCTUnwrap(decodeCard(glmCard).plan?.equivalentCapacity.first)

        // "0 tasks remaining" and "we do not know yet" look identical in a
        // progress bar and mean opposite things.
        XCTAssertNil(capacity.estimatedRemainingTasks)
        XCTAssertFalse(capacity.hasEstimate)
        XCTAssertEqual(capacity.unavailableReason, "INSUFFICIENT_SAMPLE")
        XCTAssertEqual(capacity.sampleCount, 0)
    }

    func testAnEstimateAlwaysDeclaresItselfEstimated() throws {
        let json = """
            {"model_id": "GLM-5.3", "window_id": "weekly", "task_class": "default",
             "estimated_remaining_tasks": 18.4, "sample_count": 12,
             "small_sample": false, "confidence": "ESTIMATED",
             "unavailable_reason": null}
            """
        let capacity = try JSONDecoder().decode(
            EquivalentCapacityView.self, from: Data(json.utf8)
        )

        XCTAssertTrue(capacity.hasEstimate)
        XCTAssertEqual(capacity.confidence, "ESTIMATED")
        XCTAssertNotEqual(capacity.confidence, "EXACT")
    }

    // MARK: - MiniMax workload scopes

    func testVideoScopeIsNeverRenderedAsACodingBalance() throws {
        // The regression fixture: general 95% beside video 60%. The card must
        // read 95%, and the video figure must not sit next to it as an equal.
        let json = """
            {
              "provider_id": "minimax-cn-coding-plan",
              "display_name": "MiniMax CN Coding Plan",
              "connection_state": "CONNECTED",
              "quota_state": "OBSERVED",
              "confidence": "EXACT",
              "credential_source": "OPENCODE_AUTH_STORE",
              "plan": {
                "provider_id": "minimax", "plan_id": "token-plan",
                "display_name": "MiniMax Token Plan",
                "quota_semantics": "SHARED_POOL",
                "pool_id": "minimax-token-plan-cn",
                "resource_kind": "TOKEN_PLAN_INCLUDED_QUOTA",
                "shared_across_models": true, "unit_kind": "PROVIDER_UNITS",
                "covered_model_ids": [],
                "state": "AVAILABLE", "confidence": "EXACT",
                "active_workload_scope": "CODING_TEXT",
                "workload_scope_notes": ["VIDEO_SCOPE_IGNORED_FOR_CODING"],
                "windows": [
                  {"window_id": "5h", "window_kind": "FIVE_HOUR", "state": "AVAILABLE",
                   "confidence": "EXACT", "remaining_fraction": 0.95},
                  {"window_id": "weekly", "window_kind": "WEEKLY", "state": "AVAILABLE",
                   "confidence": "EXACT", "remaining_fraction": 0.95}
                ],
                "binding_window": {"window_id": "5h", "window_kind": "FIVE_HOUR",
                  "remaining_fraction": 0.95, "reason": "SCARCEST_COMPARABLE_WINDOW",
                  "confidence": "EXACT"},
                "model_consumption": [],
                "model_equivalents": [
                  {"scope_id": "general", "scope_kind": "PROVIDER_RESOURCE_SCOPE",
                   "workload_scope": "CODING_TEXT",
                   "window_id": "5h", "remaining_fraction": 0.95,
                   "unit_kind": "UNKNOWN", "confidence": "EXACT"},
                  {"scope_id": "video", "scope_kind": "PROVIDER_RESOURCE_SCOPE",
                   "workload_scope": "VIDEO_GENERATION",
                   "window_id": "5h", "remaining_fraction": 0.6,
                   "unit_kind": "UNKNOWN", "confidence": "EXACT"}
                ],
                "equivalent_capacity": []
              }
            }
            """
        let card = try decodeCard(json)
        let plan = try XCTUnwrap(card.plan)

        // Both coding windows carry the provider's own general figure.
        XCTAssertEqual(card.quotaState, "OBSERVED")
        for window in plan.windows {
            XCTAssertTrue(window.isReadable)
            XCTAssertEqual(window.remainingFraction ?? 0, 0.95, accuracy: 0.0001)
        }
        // Neither averaged with video (0.775) nor limited by it (0.6).
        XCTAssertNotEqual(plan.bindingWindow?.remainingFraction, 0.6)

        // The two scopes are separated, so video cannot read as a coding bar.
        XCTAssertEqual(plan.inScopeEquivalents.map(\.scopeId), ["general"])
        XCTAssertEqual(plan.outOfScopeEquivalents.map(\.scopeId), ["video"])
        // ...and video is still present, not deleted.
        XCTAssertEqual(plan.modelEquivalents.count, 2)
        XCTAssertEqual(plan.workloadScopeNotes, ["VIDEO_SCOPE_IGNORED_FOR_CODING"])
        XCTAssertEqual(plan.activeWorkloadScope, "CODING_TEXT")
    }

    func testWorkloadScopesReadDifferentlyToTheOwner() {
        // A card that labels coding and video identically would defeat the
        // separation above.
        XCTAssertNotEqual(
            L10n.quotaWorkloadScope("CODING_TEXT"),
            L10n.quotaWorkloadScope("VIDEO_GENERATION")
        )
        XCTAssertFalse(L10n.quotaWorkloadScope("CODING_TEXT").isEmpty)
    }

    func testCodingUnknownReasonsNeverBlameAnotherWorkload() {
        // Every retired code must fall through to the generic sentence rather
        // than keep a dedicated one: the product no longer says "quota varies
        // by model" when two workload scopes simply differ.
        let generic = L10n.quotaFailureReason("SOMETHING_UNMAPPED")
        XCTAssertEqual(L10n.quotaFailureReason("QUOTA_VARIES_BY_MODEL"), generic)
        XCTAssertEqual(L10n.quotaFailureReason("SHARED_POOL_VIEWED_PER_MODEL"), generic)
        // ...while the codes that replaced them say what is actually missing.
        XCTAssertNotEqual(L10n.quotaFailureReason("GENERAL_QUOTA_NOT_AVAILABLE"), generic)
        XCTAssertNotEqual(
            L10n.quotaFailureReason("GENERAL_WINDOW_SEMANTICS_UNKNOWN"), generic
        )
        XCTAssertNotEqual(L10n.quotaFailureReason("CODING_SCOPE_VIEWS_DISAGREE"), generic)
    }

    // MARK: - Compatibility

    func testACardFromAnOlderDaemonStillDecodes() throws {
        // A freshly built app may briefly face a daemon predating these keys.
        // Failing to decode would blank the very page that exists to stay
        // visible when something is wrong.
        let card = try decodeCard(
            """
            {"provider_id": "zai-coding-plan", "display_name": "GLM / Z.AI",
             "connection_state": "CONNECTED", "quota_state": "UNKNOWN",
             "confidence": "UNKNOWN"}
            """
        )

        XCTAssertEqual(card.credentialSource, "NONE")
        XCTAssertNil(card.plan)
        XCTAssertTrue(card.quotaPools.isEmpty)
    }

    func testAPlanFromADaemonPredatingWorkloadScopesStillDecodes() throws {
        // The workload-scope keys are new. A card without them must decode to
        // UNKNOWN rather than fail, or the Quota page blanks during a rollout.
        let plan = try XCTUnwrap(
            try decodeCard(
                """
                {"provider_id": "minimax-cn-coding-plan", "display_name": "MiniMax",
                 "connection_state": "CONNECTED", "quota_state": "UNKNOWN",
                 "confidence": "UNKNOWN",
                 "plan": {"provider_id": "minimax", "plan_id": "token-plan",
                  "display_name": "MiniMax Token Plan", "quota_semantics": "SHARED_POOL",
                  "pool_id": "minimax-token-plan-cn", "resource_kind": "UNKNOWN",
                  "shared_across_models": true, "unit_kind": "UNKNOWN",
                  "covered_model_ids": [], "state": "UNKNOWN", "confidence": "UNKNOWN",
                  "windows": [], "model_consumption": [], "equivalent_capacity": [],
                  "model_equivalents": [
                    {"scope_id": "general", "scope_kind": "PROVIDER_RESOURCE_SCOPE",
                     "window_id": "5h", "remaining_fraction": 0.95,
                     "unit_kind": "UNKNOWN", "confidence": "EXACT"}]}}
                """
            ).plan
        )

        XCTAssertEqual(plan.activeWorkloadScope, "UNKNOWN")
        XCTAssertTrue(plan.workloadScopeNotes.isEmpty)
        XCTAssertEqual(plan.modelEquivalents.first?.workloadScope, "UNKNOWN")
        // An unclassified scope is in-scope: a provider naming its entries
        // after models is still describing the coding pool.
        XCTAssertEqual(plan.inScopeEquivalents.count, 1)
        XCTAssertTrue(plan.outOfScopeEquivalents.isEmpty)
    }

    // MARK: - Localization

    func testConfidenceLevelsReadDifferentlyToTheOwner() {
        let exact = L10n.quotaConfidenceLevel("EXACT")
        let estimated = L10n.quotaConfidenceLevel("ESTIMATED")
        let unknown = L10n.quotaConfidenceLevel("UNKNOWN")

        // An estimate that reads identically to an official balance is the
        // defect this hierarchy exists to prevent.
        XCTAssertNotEqual(exact, estimated)
        XCTAssertNotEqual(estimated, unknown)
        XCTAssertNotEqual(exact, unknown)
    }

    func testCredentialProvenanceNeverRendersAValue() {
        for source in ["ENVIRONMENT", "OPENCODE_AUTH_STORE", "NONE", "anything-else"] {
            let rendered = L10n.quotaCredentialSource(source)
            XCTAssertFalse(rendered.isEmpty)
        }
        // The two real sources must be distinguishable to the owner.
        XCTAssertNotEqual(
            L10n.quotaCredentialSource("ENVIRONMENT"),
            L10n.quotaCredentialSource("OPENCODE_AUTH_STORE")
        )
    }

    func testSharedPlanStringsExistInBothCatalogs() {
        let keys = [
            "quota.plan.sharedQuota", "quota.plan.sharedModels",
            "quota.plan.modelUsage", "quota.plan.modelUsageFooter",
            "quota.plan.equivalents", "quota.plan.equivalentsFooter",
            "quota.plan.estimatedCapacity", "quota.plan.estimatedCapacityFooter",
            "quota.plan.insufficientHistory", "quota.plan.noPlanFigure",
            "quota.plan.credentialSource", "quota.binding.title",
            "quota.confidence.exact", "quota.confidence.estimated",
            "quota.plan.workloadScopeLabel", "quota.plan.workload.codingText",
            "quota.plan.workload.video", "quota.plan.otherScopes",
            "quota.plan.otherScopesFooter",
            "quota.reason.codingScopeUnavailable",
            "quota.reason.codingWindowSemanticsUnknown",
        ]
        for key in keys {
            XCTAssertNotNil(
                L10n.catalogString(key: key, language: "en"), "missing en entry for \(key)"
            )
            let chinese = L10n.catalogString(key: key, language: "zh-Hans")
            XCTAssertNotNil(chinese, "missing zh-Hans entry for \(key)")
            XCTAssertNotEqual(
                chinese, L10n.catalogString(key: key, language: "en"),
                "zh-Hans must not copy English for \(key)"
            )
        }
    }

    func testInsufficientHistoryUsesTheOwnersLanguage() {
        // The owner-visible sentence for "no estimate" is pinned, because an
        // empty or English string here is how a missing estimate turns into a
        // number-shaped blank.
        XCTAssertEqual(
            L10n.catalogString(key: "quota.plan.insufficientHistory", language: "zh-Hans"),
            "历史数据不足，暂不估算"
        )
    }
}
