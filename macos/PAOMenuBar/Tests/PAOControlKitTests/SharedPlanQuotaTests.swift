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

    // MARK: - MiniMax per-model views of one pool

    func testDisagreeingModelViewsRenderAsEquivalentsNotBalances() throws {
        let json = """
            {
              "provider_id": "minimax-cn-coding-plan",
              "display_name": "MiniMax CN Coding Plan",
              "connection_state": "CONNECTED",
              "quota_state": "UNKNOWN",
              "confidence": "UNKNOWN",
              "failure_reason": "SHARED_POOL_VIEWED_PER_MODEL",
              "credential_source": "OPENCODE_AUTH_STORE",
              "plan": {
                "provider_id": "minimax", "plan_id": "token-plan",
                "display_name": "MiniMax Token Plan",
                "quota_semantics": "SHARED_POOL",
                "pool_id": "minimax-token-plan-cn",
                "resource_kind": "TOKEN_PLAN_INCLUDED_QUOTA",
                "shared_across_models": true, "unit_kind": "PROVIDER_UNITS",
                "covered_model_ids": ["MiniMax-M3", "MiniMax-M2.7"],
                "state": "AVAILABLE", "confidence": "UNKNOWN",
                "unknown_reason": "SHARED_POOL_VIEWED_PER_MODEL",
                "windows": [
                  {"window_id": "5h", "window_kind": "FIVE_HOUR", "state": "UNKNOWN",
                   "confidence": "UNKNOWN", "remaining_fraction": null},
                  {"window_id": "weekly", "window_kind": "WEEKLY", "state": "AVAILABLE",
                   "confidence": "EXACT", "remaining_fraction": 0.6}
                ],
                "binding_window": {"window_id": "weekly", "window_kind": "WEEKLY",
                  "remaining_fraction": 0.6, "reason": "ONLY_KNOWN_WINDOW",
                  "confidence": "ESTIMATED"},
                "model_consumption": [],
                "model_equivalents": [
                  {"model_id": "MiniMax-M3", "window_id": "5h",
                   "remaining_fraction": 0.95, "unit_kind": "UNKNOWN",
                   "confidence": "EXACT"},
                  {"model_id": "MiniMax-M2.7", "window_id": "5h",
                   "remaining_fraction": 0.6, "unit_kind": "UNKNOWN",
                   "confidence": "EXACT"}
                ],
                "equivalent_capacity": []
              }
            }
            """
        let plan = try XCTUnwrap(decodeCard(json).plan)

        // Partial knowledge is kept: the underivable window is UNKNOWN and
        // draws no bar, while the window every model agrees on still renders.
        let fiveHour = try XCTUnwrap(plan.windows.first { $0.windowId == "5h" })
        let weekly = try XCTUnwrap(plan.windows.first { $0.windowId == "weekly" })
        XCTAssertFalse(fiveHour.isReadable)
        XCTAssertNil(fiveHour.remainingFraction)
        XCTAssertTrue(weekly.isReadable)
        XCTAssertTrue(plan.hasReadableWindow)

        // The per-model figures survive, in the section labelled as views.
        XCTAssertEqual(plan.modelEquivalents.count, 2)
        // ...and produce no per-model windows that could be drawn as balances.
        XCTAssertEqual(plan.windows.count, 2)
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
            "quota.reason.sharedPoolPerModel",
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
