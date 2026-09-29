from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

NUMBER_WORDS = {
    "один": 1,
    "одна": 1,
    "одно": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}

CLAUSE_CONNECTORS = {"и", "and", "then", "затем", "плюс", "plus"}


@dataclass(slots=True)
class ParseCandidate:
    id: int
    name: str
    aliases: list[str] = field(default_factory=list)
    modifiers: dict[int, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedItem:
    menu_item_id: int
    name: str
    quantity: int
    modifier_ids: list[int]
    confidence: float
    source_text: str = ""


@dataclass(slots=True)
class MenuSuggestion:
    menu_item_id: int
    name: str
    confidence: float


@dataclass(slots=True)
class UnmatchedClause:
    text: str
    quantity: int
    suggestions: list[MenuSuggestion] = field(default_factory=list)


@dataclass(slots=True)
class OrderTextAnalysis:
    items: list[ParsedItem] = field(default_factory=list)
    unmatched: list[UnmatchedClause] = field(default_factory=list)


@dataclass(slots=True)
class _CandidateMatch:
    candidate: ParseCandidate
    phrase: str
    start: int
    end: int
    confidence: float


def _normalize(text: str) -> str:
    normalized = re.sub(r"[^\w\s]", " ", text.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _phrase_span(text: str, phrase: str) -> tuple[int, int] | None:
    match = re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text)
    return match.span() if match else None


def _candidate_phrases(candidate: ParseCandidate) -> list[str]:
    phrases = {_normalize(candidate.name), *(_normalize(alias) for alias in candidate.aliases)}
    return sorted((phrase for phrase in phrases if phrase), key=len, reverse=True)


def _exact_matches(text: str, candidates: list[ParseCandidate]) -> list[_CandidateMatch]:
    matches: list[_CandidateMatch] = []
    for candidate in candidates:
        best: tuple[str, tuple[int, int]] | None = None
        for phrase in _candidate_phrases(candidate):
            span = _phrase_span(text, phrase)
            if span and (best is None or len(phrase) > len(best[0])):
                best = (phrase, span)
        if best:
            phrase, (start, end) = best
            matches.append(_CandidateMatch(candidate, phrase, start, end, 1.0))
    return matches


def _split_clauses(text: str, candidates: list[ParseCandidate]) -> list[str]:
    """Split an order without breaking item names such as ``gin and tonic``."""
    normalized = _normalize(re.sub(r"[,;.!?]+", " then ", text))
    if not normalized:
        return []

    protected_spans = [(match.start, match.end) for match in _exact_matches(normalized, candidates)]
    split_at: list[tuple[int, int]] = []
    connector_pattern = "|".join(
        re.escape(connector) for connector in sorted(CLAUSE_CONNECTORS, key=len, reverse=True)
    )
    for match in re.finditer(rf"(?<!\w)(?:{connector_pattern})(?!\w)", normalized):
        if not any(start <= match.start() and match.end() <= end for start, end in protected_spans):
            split_at.append(match.span())

    clauses: list[str] = []
    cursor = 0
    for start, end in split_at:
        if clause := normalized[cursor:start].strip():
            clauses.append(clause)
        cursor = end
    if clause := normalized[cursor:].strip():
        clauses.append(clause)
    return clauses


def _best_candidate(
    clause: str,
    candidates: list[ParseCandidate],
    fuzzy_threshold: int,
) -> _CandidateMatch | None:
    exact = _exact_matches(clause, candidates)
    if exact:
        return max(exact, key=lambda match: (len(match.phrase), -match.start))

    scored: list[tuple[ParseCandidate, str, float]] = []
    for candidate in candidates:
        for phrase in _candidate_phrases(candidate):
            scored.append((candidate, phrase, fuzz.partial_ratio(phrase, clause)))
    if not scored:
        return None
    candidate, phrase, score = max(scored, key=lambda row: row[2])
    if score < fuzzy_threshold:
        return None
    # Fuzzy matching has no exact span; searching the full clause still finds
    # the quantity that normally precedes the misspelled or inflected item.
    return _CandidateMatch(candidate, phrase, len(clause), len(clause), score / 100)


def _quantity(clause: str, before_position: int | None = None) -> int:
    searchable = clause if before_position is None else clause[:before_position]
    matches: list[tuple[int, int]] = []
    for match in re.finditer(r"(?<!\w)([1-9])(?!\w)", searchable):
        matches.append((match.start(), int(match.group(1))))
    for match in re.finditer(r"\b\w+\b", searchable):
        if match.group() in NUMBER_WORDS:
            matches.append((match.start(), NUMBER_WORDS[match.group()]))
    return max(matches, default=(-1, 1), key=lambda row: row[0])[1]


def _modifier_ids(clause: str, candidate: ParseCandidate) -> list[int]:
    found: list[int] = []
    for modifier_id, aliases in candidate.modifiers.items():
        phrases = {_normalize(alias) for alias in aliases}
        if any(phrase and _phrase_span(clause, phrase) for phrase in phrases):
            found.append(modifier_id)
    return found


def _suggestions(
    clause: str,
    candidates: list[ParseCandidate],
    *,
    limit: int = 3,
    minimum_score: int = 42,
) -> list[MenuSuggestion]:
    scored: list[MenuSuggestion] = []
    for candidate in candidates:
        score = max(
            (fuzz.WRatio(phrase, clause) for phrase in _candidate_phrases(candidate)),
            default=0,
        )
        if score >= minimum_score:
            scored.append(
                MenuSuggestion(
                    menu_item_id=candidate.id,
                    name=candidate.name,
                    confidence=score / 100,
                )
            )
    scored.sort(key=lambda suggestion: (-suggestion.confidence, suggestion.name))
    return scored[:limit]


def analyze_order_text(
    text: str,
    candidates: list[ParseCandidate],
    fuzzy_threshold: int = 78,
) -> OrderTextAnalysis:
    analysis = OrderTextAnalysis()
    previous_candidate: ParseCandidate | None = None

    for clause in _split_clauses(text, candidates):
        match = _best_candidate(clause, candidates, fuzzy_threshold)
        if match is not None:
            candidate = match.candidate
            analysis.items.append(
                ParsedItem(
                    menu_item_id=candidate.id,
                    name=candidate.name,
                    quantity=_quantity(clause, match.start),
                    modifier_ids=_modifier_ids(clause, candidate),
                    confidence=match.confidence,
                    source_text=clause,
                )
            )
            previous_candidate = candidate
            continue

        if previous_candidate is not None:
            modifier_ids = _modifier_ids(clause, previous_candidate)
            if modifier_ids:
                analysis.items.append(
                    ParsedItem(
                        menu_item_id=previous_candidate.id,
                        name=previous_candidate.name,
                        quantity=_quantity(clause),
                        modifier_ids=modifier_ids,
                        confidence=1.0,
                        source_text=clause,
                    )
                )
                continue

        analysis.unmatched.append(
            UnmatchedClause(
                text=clause,
                quantity=_quantity(clause),
                suggestions=_suggestions(clause, candidates),
            )
        )

    return analysis


def parse_order_text(
    text: str,
    candidates: list[ParseCandidate],
    fuzzy_threshold: int = 78,
) -> list[ParsedItem]:
    return analyze_order_text(text, candidates, fuzzy_threshold).items
