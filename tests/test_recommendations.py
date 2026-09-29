from app.services.recommendations import RecommendationCandidate, rank_recommendations


def test_recommendations_prefer_matching_taste_and_base() -> None:
    candidates = [
        RecommendationCandidate(
            id=1,
            name="Gin and Tonic",
            profile={"tastes": ["fresh", "bitter"], "base": "gin", "popularity": 5},
        ),
        RecommendationCandidate(
            id=2,
            name="Sweet Rum",
            profile={"tastes": ["sweet"], "base": "rum", "popularity": 10},
        ),
    ]
    ranked = rank_recommendations("Хочу освежающий напиток с джином", candidates)
    assert ranked[0][0].id == 1


def test_recommendations_filter_unavailable_items() -> None:
    candidates = [
        RecommendationCandidate(
            id=1,
            name="Unavailable",
            profile={"tastes": ["sweet"]},
            available=False,
        )
    ]
    assert rank_recommendations("сладкое", candidates) == []


def test_non_alcoholic_request_filters_alcohol() -> None:
    candidates = [
        RecommendationCandidate(
            id=1,
            name="Cocktail",
            profile={"tastes": ["fresh"], "alcoholic": True},
        ),
        RecommendationCandidate(
            id=2,
            name="Lemonade",
            profile={"tastes": ["fresh"], "alcoholic": False, "base": "non_alcoholic"},
        ),
    ]
    ranked = rank_recommendations("безалкогольный освежающий", candidates)
    assert [row[0].id for row in ranked] == [2]


def test_recommendations_use_strength_and_mood() -> None:
    candidates = [
        RecommendationCandidate(
            id=1,
            name="Party drink",
            profile={"strength": "strong", "moods": ["party"]},
        ),
        RecommendationCandidate(
            id=2,
            name="Quiet drink",
            profile={"strength": "light", "moods": ["relaxed"]},
        ),
    ]

    ranked = rank_recommendations("Хочу крепкое для вечеринки", candidates)

    assert ranked[0][0].id == 1
