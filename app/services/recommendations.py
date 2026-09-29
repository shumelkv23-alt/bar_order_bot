from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TASTE_KEYWORDS = {
    "sweet": {"сладкий", "сладкое", "сладкого", "sweet"},
    "sour": {"кислый", "кислое", "кислого", "цитрусовый", "sour", "citrus"},
    "bitter": {"горький", "горькое", "bitter"},
    "fresh": {
        "свежий",
        "свежее",
        "освежающий",
        "освежающее",
        "лёгкий",
        "лёгкое",
        "fresh",
        "refreshing",
        "light",
    },
    "strong": {"крепкий", "покрепче", "strong"},
}

BASE_KEYWORDS = {
    "gin": {"джин", "gin"},
    "rum": {"ром", "rum"},
    "vodka": {"водка", "vodka"},
    "whiskey": {"виски", "whisky", "whiskey"},
    "wine": {"вино", "wine"},
    "beer": {"пиво", "beer"},
    "non_alcoholic": {
        "безалкогольный",
        "безалкогольное",
        "безалкогольного",
        "без алкоголя",
        "non alcoholic",
        "alcohol free",
    },
}

STRENGTH_KEYWORDS = {
    "none": {"без алкоголя", "безалкогольное", "non alcoholic", "alcohol free"},
    "light": {"лёгкое", "легкое", "некрепкое", "light", "low alcohol"},
    "medium": {"средней крепости", "не слишком крепкое", "medium"},
    "strong": {"крепкое", "покрепче", "strong"},
}

MOOD_KEYWORDS = {
    "party": {"вечеринка", "тусовка", "веселиться", "party"},
    "relaxed": {"расслабиться", "спокойное", "отдохнуть", "relaxed", "chill"},
    "summer": {"летнее", "жарко", "освежиться", "summer", "hot day"},
    "evening": {"вечер", "вечернее", "evening"},
    "celebration": {"праздник", "отпраздновать", "celebration"},
    "dinner": {"к ужину", "с едой", "dinner"},
}


@dataclass(slots=True)
class RecommendationCandidate:
    id: int
    name: str
    profile: dict[str, Any] = field(default_factory=dict)
    available: bool = True


def extract_preferences(text: str) -> dict[str, Any]:
    normalized = text.casefold()
    tastes = {
        taste
        for taste, words in TASTE_KEYWORDS.items()
        if any(word in normalized for word in words)
    }
    bases = {
        base for base, words in BASE_KEYWORDS.items() if any(word in normalized for word in words)
    }
    strengths = {
        strength
        for strength, words in STRENGTH_KEYWORDS.items()
        if any(word in normalized for word in words)
    }
    moods = {
        mood for mood, words in MOOD_KEYWORDS.items() if any(word in normalized for word in words)
    }
    return {"tastes": tastes, "bases": bases, "strengths": strengths, "moods": moods}


def rank_recommendations(
    text: str,
    candidates: list[RecommendationCandidate],
    limit: int = 3,
) -> list[tuple[RecommendationCandidate, float, list[str]]]:
    preferences = extract_preferences(text)
    ranked: list[tuple[RecommendationCandidate, float, list[str]]] = []
    for candidate in candidates:
        if not candidate.available:
            continue
        profile = candidate.profile or {}
        if "non_alcoholic" in preferences["bases"] and profile.get("alcoholic", True):
            continue
        score = 0.0
        reasons: list[str] = []
        profile_tastes = set(profile.get("tastes", []))
        taste_matches = preferences["tastes"] & profile_tastes
        if taste_matches:
            score += 3.0 * len(taste_matches)
            reasons.append("совпадает по вкусу: " + ", ".join(sorted(taste_matches)))
        base = profile.get("base")
        if base and base in preferences["bases"]:
            score += 2.5
            reasons.append(f"подходящая основа: {base}")
        strength = profile.get("strength")
        if strength and strength in preferences["strengths"]:
            score += 2.0
            reasons.append(f"подходящая крепость: {strength}")
        profile_moods = set(profile.get("moods", []))
        mood_matches = preferences["moods"] & profile_moods
        if mood_matches:
            score += 1.5 * len(mood_matches)
            reasons.append("подходит под настроение: " + ", ".join(sorted(mood_matches)))
        score += min(float(profile.get("popularity", 0)), 10.0) * 0.05
        if not any(preferences.values()):
            score += float(profile.get("popularity", 0)) * 0.1
            reasons.append("популярный вариант")
        if score > 0:
            ranked.append((candidate, score, reasons))
    ranked.sort(key=lambda row: (-row[1], row[0].name))
    return ranked[:limit]
