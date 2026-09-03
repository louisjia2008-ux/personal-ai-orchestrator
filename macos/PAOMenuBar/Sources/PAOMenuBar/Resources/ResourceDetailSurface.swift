import SwiftUI

import PAOControlKit

/// The selected resource, in full.
///
/// Ordered by the questions the owner actually arrives with: is it reachable,
/// how much is left, what has it been doing, what happens next, what can run on
/// it, and — last, behind its own heading — where all of that came from.
///
/// Sections rather than cards. A provider is one thing; eight rounded boxes
/// would present its parts as eight unrelated widgets.
struct ResourceDetailSurface: View {
    let resource: ResourceSnapshot?
    let history: QuotaHistoryView?
    let displayNames: [String: String]
    let isRefreshingQuota: Bool
    let onRefreshQuota: (String) -> Void
    let onConnect: (String) -> Void
    var now: Date = Date()

    var body: some View {
        if let resource {
            ScrollView {
                VStack(alignment: .leading, spacing: Spacing.section) {
                    ResourceDetailHeader(
                        resource: resource,
                        isRefreshingQuota: isRefreshingQuota,
                        onRefreshQuota: onRefreshQuota,
                        onConnect: onConnect,
                        now: now
                    )
                    ResourceAvailabilitySection(resource: resource, now: now)
                    ResourceQuotaSection(resource: resource, now: now)
                    if let plan = resource.plan {
                        ResourceSection(L10n.quotaPlanSharedQuota, symbol: "creditcard") {
                            ResourcePlanSection(plan: plan)
                        }
                    }
                    ResourceSection(L10n.resourceSectionUsage, symbol: "chart.xyaxis.line") {
                        QuotaHistoryPanel(
                            series: series,
                            displayNames: displayNames,
                            retentionLimit: history?.retentionLimit ?? 0
                        )
                    }
                    QuotaProjectionSection(
                        outlooks: outlooks, displayNames: displayNames, now: now
                    )
                    ResourceExecutionTargetsSection(resource: resource)
                    ResourceModelsSection(resource: resource)
                    ResourceObservabilitySection(resource: resource)
                }
                .frame(maxWidth: ContentWidth.reading, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, DashboardLayoutMetrics.pageHorizontalPadding)
                .padding(.vertical, DashboardLayoutMetrics.pageVerticalPadding)
            }
            .background(Color(nsColor: .windowBackgroundColor))
            .accessibilityIdentifier("workspace.detail")
        } else {
            EmptyStateView(
                title: L10n.emptyResourcesNoSelectionTitle,
                symbol: "square.stack.3d.up",
                message: L10n.emptyResourcesNoSelectionMessage
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
            .background(Color(nsColor: .windowBackgroundColor))
            .accessibilityIdentifier("workspace.detail")
        }
    }

    /// History scoped to the selected resource. A provider's usage panel shows
    /// only its own series; another provider's line on this chart would answer a
    /// question nobody asked here.
    private var series: [QuotaSeries] {
        guard let resource else { return [] }
        return QuotaHistory.series(from: history, providerId: resource.providerId)
    }

    private var outlooks: [QuotaOutlook] {
        series.map { QuotaForecast.outlook(for: $0, now: now) }
    }
}

/// Identity, current availability and the actions available on this resource.
struct ResourceDetailHeader: View {
    let resource: ResourceSnapshot
    let isRefreshingQuota: Bool
    let onRefreshQuota: (String) -> Void
    let onConnect: (String) -> Void
    var now: Date = Date()

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.inner) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.element) {
                Text(resource.displayName)
                    .font(.title2.weight(.semibold))
                    .textSelection(.enabled)
                Spacer(minLength: 0)
                actions
            }
            HStack(spacing: Spacing.inner) {
                ResourceStatusLabel(
                    presentation: resource.connectionStatus,
                    text: L10n.connectionStateLabel(resource.connectionState)
                )
                ResourceChip(text: resource.kind.title)
                if let plan = resource.planSurface { ResourceChip(text: plan) }
                if let region = resource.region { ResourceChip(text: region) }
                Spacer(minLength: 0)
            }
            freshness
        }
    }

    /// Refresh actions stay separate.
    ///
    /// Refreshing discovery re-runs the catalog and credential scan and reads no
    /// quota; refreshing quota contacts each connected provider's read-only
    /// quota endpoint. They are different daemon operations with different
    /// costs, and one button labelled "Refresh" would hide which one ran.
    @ViewBuilder
    private var actions: some View {
        HStack(spacing: Spacing.inner) {
            if resource.kind == .available {
                Button {
                    onConnect(resource.providerId)
                } label: {
                    Label(L10n.resourceConnectAction, systemImage: "plus")
                }
                .buttonStyle(.borderedProminent)
            }
            if resource.kind == .connected {
                Button {
                    onRefreshQuota(resource.providerId)
                } label: {
                    if isRefreshingQuota {
                        ProgressView().controlSize(.small)
                    } else {
                        Label(L10n.resourceRefreshQuota, systemImage: "arrow.clockwise")
                    }
                }
                .buttonStyle(.bordered)
                .disabled(isRefreshingQuota)
                .help(L10n.resourceRefreshQuotaHelp)
            }
        }
    }

    /// When the quota reading was taken. States its own absence rather than
    /// omitting itself, so "never read" and "read a moment ago" are both
    /// visible facts.
    @ViewBuilder
    private var freshness: some View {
        let observedAt = resource.quotaObservedAt ?? resource.quotaLastRefreshAt
        if let observedAt, let date = TaskTiming.parseTimestamp(observedAt) {
            Label {
                Text(
                    QuotaFormat.age(of: date, now: now).map(L10n.resourceObservedAge)
                        ?? Timestamps.friendly(observedAt, now: now)
                )
            } icon: {
                Image(systemName: "clock")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .help(Timestamps.absolute(date))
        } else {
            Label(L10n.resourceFreshnessUnknown, systemImage: "clock.badge.questionmark")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }
}

/// Whether this resource can be reached, and on what evidence.
struct ResourceAvailabilitySection: View {
    let resource: ResourceSnapshot
    var now: Date = Date()

    var body: some View {
        ResourceSection(L10n.resourceSectionAvailability, symbol: "link") {
            VStack(alignment: .leading, spacing: Spacing.inner) {
                ResourceFieldRow(
                    label: L10n.quotaConnectionStatus,
                    value: L10n.connectionStateLabel(resource.connectionState)
                )
                if let authState = resource.authState {
                    ResourceFieldRow(
                        label: L10n.providerAuthStatus,
                        value: L10n.authStateLabel(authState)
                    )
                }
                // Whether the daemon has quota telemetry at all — a fact about
                // observability, distinct from what any binding currently says.
                LabeledContent(L10n.quotaStatusTitle) {
                    ResourceStatusLabel(
                        presentation: resource.isQuotaObserved
                            ? StatusPresentation(tone: .positive, symbol: "checkmark.circle")
                            : StatusPresentation(tone: .unknown, symbol: "questionmark.circle"),
                        text: resource.isQuotaObserved
                            ? L10n.resourceQuotaObservable
                            : L10n.resourceQuotaNotObservable
                    )
                }
                if let connection = resource.connection {
                    ResourceFieldRow(
                        label: L10n.providersRuntime,
                        value: L10n.runtimeStateLabel(connection.runtimeState)
                    )
                    LabeledContent(L10n.providerExecutionStatus) {
                        ResourceStatusLabel(
                            presentation: connection.executionVerified
                                ? StatusPresentation(tone: .positive, symbol: "checkmark.seal")
                                : StatusPresentation(tone: .caution, symbol: "seal"),
                            text: connection.executionVerified
                                ? L10n.providerExecutionVerified
                                : L10n.providerNotExecutionVerified
                        )
                    }
                }
                // A refresh that failed is a distinct state from quota being
                // unknown: one is a broken read, the other an unread surface.
                if let status = resource.quota?.lastRefreshStatus,
                    status != "OK", status != "SUCCESS"
                {
                    ResourceNotice(
                        text: L10n.resourceRefreshFailed(status),
                        symbol: "exclamationmark.triangle",
                        tone: .caution
                    )
                }
            }
        }
    }
}

/// Where every figure above came from.
///
/// Machine identifiers, measurement sources and credential provenance. Raw ISO
/// timestamps live here too — verbatim is the point in this section, and
/// nowhere else.
struct ResourceObservabilitySection: View {
    let resource: ResourceSnapshot

    var body: some View {
        ResourceSection(L10n.resourceSectionObservability, symbol: "scope") {
            DisclosureGroup(L10n.advancedDetails) {
                VStack(alignment: .leading, spacing: Spacing.tight) {
                    ResourceFieldRow(
                        label: L10n.providersProviderId,
                        value: resource.providerId,
                        monospaced: true
                    )
                    if let quota = resource.quota {
                        ResourceFieldRow(
                            label: L10n.measurementSource, value: quota.measurementSource
                        )
                        ResourceFieldRow(
                            label: L10n.confidenceLabel, value: quota.confidence
                        )
                        ResourceFieldRow(
                            label: L10n.quotaLastAttempt, value: quota.lastRefreshStatus
                        )
                        // Provenance of the credential used for the read. Never
                        // the credential.
                        ResourceFieldRow(
                            label: L10n.quotaPlanCredentialSource,
                            value: L10n.quotaCredentialSource(quota.credentialSource)
                        )
                        ResourceFieldRow(
                            label: L10n.observedAt, value: quota.observedAt, monospaced: true
                        )
                        if let reason = quota.failureReason {
                            ResourceFieldRow(label: L10n.reasonCode, value: reason)
                        }
                    }
                    if let connection = resource.connection {
                        ResourceFieldRow(
                            label: L10n.providersCredentialReference,
                            value: connection.credentialReferenceType
                        )
                        if let reason = connection.lastReasonCode {
                            ResourceFieldRow(label: L10n.reasonLabel, value: reason)
                        }
                    }
                    if let health = resource.health {
                        ResourceFieldRow(
                            label: L10n.providersAccountsLabel,
                            value: "\(health.accountCount)"
                        )
                        if let evidence = health.evidenceSource {
                            ResourceFieldRow(label: L10n.evidenceLabel, value: evidence)
                        }
                    }
                    if let plan = resource.plan {
                        ResourceFieldRow(
                            label: L10n.quotaPlanPoolId, value: plan.poolId, monospaced: true
                        )
                        ResourceFieldRow(
                            label: L10n.quotaPlanSharedSemantics, value: plan.quotaSemantics
                        )
                        // Sanitized codes explaining scopes this workload does
                        // not read. They record an absence; they are not faults.
                        ForEach(plan.workloadScopeNotes, id: \.self) { note in
                            Text(note)
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(.tertiary)
                        }
                    }
                    ForEach(resource.quotaBindings) { binding in
                        ResourceFieldRow(
                            label: L10n.quotaPoolId,
                            value: "\(binding.series.quotaPoolId) / \(binding.series.windowId)",
                            monospaced: true
                        )
                    }
                }
                .padding(.top, Spacing.tight)
            }
        }
    }
}
