import Foundation

public enum FindingState: String, Codable, CaseIterable, Sendable {
    case supported, unknown, conflict

    public var label: String {
        switch self {
        case .supported: return "Есть основания"
        case .unknown: return "Неизвестно"
        case .conflict: return "Противоречие"
        }
    }
}

public enum ReviewDecision: String, Codable, CaseIterable, Sendable {
    case undecided, discuss, research, watch

    public var label: String {
        switch self {
        case .undecided: return "Не решено"
        case .discuss: return "Обсудить"
        case .research: return "Доисследовать"
        case .watch: return "Наблюдать"
        }
    }
}

public struct EvidenceProvenance: Codable, Sendable {
    public var provider: String
    public var query: String
    public var retrievedAt: String
    public var channel: String?
    public var collectionID: String
    public var contentType: String

    public init(provider: String, query: String, retrievedAt: String, channel: String?, collectionID: String, contentType: String) {
        self.provider = provider; self.query = query; self.retrievedAt = retrievedAt
        self.channel = channel; self.collectionID = collectionID; self.contentType = contentType
    }
}

public struct Evidence: Identifiable, Codable, Sendable {
    public var id: String
    public var title: String
    public var excerpt: String
    public var url: String?
    public var dateLabel: String
    public var kind: String
    public var isDemo: Bool
    public var provenance: EvidenceProvenance?

    public init(id: String, title: String, excerpt: String, url: String?, dateLabel: String, kind: String, isDemo: Bool, provenance: EvidenceProvenance? = nil) {
        self.id = id; self.title = title; self.excerpt = excerpt; self.url = url
        self.dateLabel = dateLabel; self.kind = kind; self.isDemo = isDemo
        self.provenance = provenance
    }
}

public struct SignalParameter: Identifiable, Codable, Sendable {
    public var id: String
    public var title: String
    public var group: String
    public var value: String
    public var state: FindingState
    public var evidenceIDs: [String]

    public init(id: String, title: String, group: String, value: String, state: FindingState, evidenceIDs: [String]) {
        self.id = id; self.title = title; self.group = group; self.value = value
        self.state = state; self.evidenceIDs = evidenceIDs
    }
}

public struct Hypothesis: Identifiable, Codable, Sendable {
    public var id: String
    public var title: String
    public var summary: String
    public var alternative: String
    public var question: String
    public var parameterIDs: [String]
    public var state: FindingState

    public init(id: String, title: String, summary: String, alternative: String, question: String, parameterIDs: [String], state: FindingState) {
        self.id = id; self.title = title; self.summary = summary; self.alternative = alternative
        self.question = question; self.parameterIDs = parameterIDs; self.state = state
    }
}

public struct CompanyDossier: Identifiable, Codable, Sendable {
    public var id: String
    public var name: String
    public var subtitle: String
    public var website: String?
    public var initials: String
    public var isDemo: Bool
    public var evidence: [Evidence]
    public var parameters: [SignalParameter]
    public var hypotheses: [Hypothesis]
    public var discovery: CompanyDiscoveryMetadata?

    public init(id: String, name: String, subtitle: String, website: String?, initials: String, isDemo: Bool, evidence: [Evidence], parameters: [SignalParameter], hypotheses: [Hypothesis], discovery: CompanyDiscoveryMetadata? = nil) {
        self.id = id; self.name = name; self.subtitle = subtitle; self.website = website
        self.initials = initials; self.isDemo = isDemo; self.evidence = evidence
        self.parameters = parameters; self.hypotheses = hypotheses
        self.discovery = discovery
    }
}

public struct CompanyReview: Codable, Sendable {
    public var question: String?
    public var questionsByHypothesis: [String: String]?
    public var note: String
    public var decision: ReviewDecision
    public var addedEvidence: [Evidence]
    public var checkedEvidenceIDs: [String]
    public var favoriteEvidenceIDs: [String]?

    public init(question: String? = nil, note: String = "", decision: ReviewDecision = .undecided, addedEvidence: [Evidence] = [], checkedEvidenceIDs: [String] = [], questionsByHypothesis: [String: String]? = nil) {
        self.question = question; self.questionsByHypothesis = questionsByHypothesis
        self.note = note; self.decision = decision
        self.addedEvidence = addedEvidence; self.checkedEvidenceIDs = checkedEvidenceIDs
    }

    public func question(for hypothesisID: String) -> String? {
        if let specificQuestion = questionsByHypothesis?[hypothesisID] { return specificQuestion }
        return hypothesisID == "transformation" ? question : nil
    }

    public mutating func setQuestion(_ value: String?, for hypothesisID: String) {
        var questions = questionsByHypothesis ?? [:]
        questions[hypothesisID] = value
        questionsByHypothesis = questions.isEmpty ? nil : questions
        if hypothesisID == "transformation" { question = nil }
    }

    public static var empty: CompanyReview { CompanyReview() }

    public var favoriteIDs: Set<String> { Set(favoriteEvidenceIDs ?? addedEvidence.map(\.id)) }

    public mutating func toggleFavorite(evidenceIDs: [String]) {
        var favorites = favoriteIDs
        if favorites.isDisjoint(with: evidenceIDs) { favorites.formUnion(evidenceIDs) }
        else { favorites.subtract(evidenceIDs) }
        favoriteEvidenceIDs = favorites.sorted()
    }
}

public struct WorkspaceSnapshot: Codable, Sendable {
    public var version: Int
    public var reviews: [String: CompanyReview]

    public init(version: Int = 1, reviews: [String: CompanyReview] = [:]) {
        self.version = version; self.reviews = reviews
    }
}
