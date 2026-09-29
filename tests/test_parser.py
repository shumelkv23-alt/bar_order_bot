from app.services.parser import ParseCandidate, analyze_order_text, parse_order_text


def candidates() -> list[ParseCandidate]:
    return [
        ParseCandidate(
            id=1,
            name="Мохито",
            aliases=["mojito"],
            modifiers={
                10: ["без льда", "no ice"],
                11: ["безалкогольный", "alcohol free", "alcohol-free"],
            },
        ),
        ParseCandidate(
            id=2,
            name="Кола",
            aliases=["cola", "coke"],
            modifiers={10: ["без льда", "no ice"]},
        ),
        ParseCandidate(
            id=3,
            name="Джин-тоник",
            aliases=["джин тоник", "gin tonic", "gin and tonic"],
            modifiers={10: ["без льда", "no ice"]},
        ),
    ]


def test_parser_finds_quantity_and_modifier() -> None:
    parsed = parse_order_text("Два мохито без льда", candidates())
    mojito = next(item for item in parsed if item.menu_item_id == 1)
    assert mojito.quantity == 2
    assert 10 in mojito.modifier_ids
    assert mojito.confidence == 1.0


def test_parser_supports_english_aliases() -> None:
    parsed = parse_order_text("one mojito and one cola", candidates())
    assert {item.menu_item_id for item in parsed} == {1, 2}


def test_parser_returns_empty_for_unknown_drink() -> None:
    assert parse_order_text("чай с лимоном", candidates()) == []


def test_quantity_and_modifier_do_not_leak_to_next_item() -> None:
    parsed = parse_order_text("два мохито без льда и джин-тоник", candidates())

    assert [(item.menu_item_id, item.quantity, item.modifier_ids) for item in parsed] == [
        (1, 2, [10]),
        (3, 1, []),
    ]


def test_parser_repeats_previous_drink_for_modifier_only_clause() -> None:
    parsed = parse_order_text("Two mojitos, one alcohol-free", candidates())

    assert [(item.menu_item_id, item.quantity, item.modifier_ids) for item in parsed] == [
        (1, 2, []),
        (1, 1, [11]),
    ]


def test_connector_inside_drink_name_is_not_a_clause_boundary() -> None:
    parsed = parse_order_text("one gin and tonic and one cola no ice", candidates())

    assert [(item.menu_item_id, item.quantity, item.modifier_ids) for item in parsed] == [
        (3, 1, []),
        (2, 1, [10]),
    ]


def test_analysis_keeps_unknown_clause_and_suggests_similar_menu_items() -> None:
    analysis = analyze_order_text("два мохито и одну маргариту", candidates())

    assert [(item.menu_item_id, item.quantity) for item in analysis.items] == [(1, 2)]
    assert len(analysis.unmatched) == 1
    assert analysis.unmatched[0].text == "одну маргариту"
    assert analysis.unmatched[0].quantity == 1
    assert analysis.unmatched[0].suggestions
