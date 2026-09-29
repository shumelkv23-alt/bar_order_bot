"""Server-graded Halloween and programming quiz for the secret menu."""

from __future__ import annotations

from typing import Any

# Correct options stay on the server; the guest only receives labels and IDs.
QUESTIONS = (
    (
        "halloween_date",
        "В какой день отмечают Хеллоуин?",
        "On which date is Halloween celebrated?",
        (("oct31", "31 октября", "October 31"),
         ("nov1", "1 ноября", "November 1"),
         ("dec25", "25 декабря", "December 25")),
        "oct31",
    ),
    (
        "lantern",
        "Из чего чаще всего вырезают фонарь Джека?",
        "What is a jack-o'-lantern usually carved from?",
        (("pumpkin", "Тыква", "Pumpkin"),
         ("melon", "Дыня", "Melon"),
         ("pineapple", "Ананас", "Pineapple")),
        "pumpkin",
    ),
    (
        "trick_or_treat",
        "Что говорят дети, обходя дома на Хеллоуин?",
        "What do children say when visiting homes on Halloween?",
        (("trick", "Сладость или гадость", "Trick or treat"),
         ("cheers", "Ваше здоровье", "Cheers"),
         ("goodnight", "Спокойной ночи", "Good night")),
        "trick",
    ),
    (
        "html",
        "Для чего используют HTML?",
        "What is HTML used for?",
        (("structure", "Для структуры веб-страницы", "Structuring a web page"),
         ("database", "Для хранения базы данных", "Storing a database"),
         ("painting", "Для рисования иллюстраций", "Painting illustrations")),
        "structure",
    ),
    (
        "python_comment",
        "Как начинается однострочный комментарий в Python?",
        "How does a single-line Python comment begin?",
        (("hash", "С символа #", "With #"),
         ("slash", "С символов //", "With //"),
         ("angle", "С символов <!--", "With <!--")),
        "hash",
    ),
    (
        "git",
        "Что помогает отслеживать Git?",
        "What does Git help track?",
        (("changes", "Изменения в файлах проекта", "Changes to project files"),
         ("weather", "Прогноз погоды", "Weather forecasts"),
         ("orders", "Заказы в баре", "Bar orders")),
        "changes",
    ),
)


def public_questions(language: str) -> list[dict[str, Any]]:
    english = language == "en"
    return [
        {
            "id": question_id,
            "question": prompt_en if english else prompt_ru,
            "options": [
                {"id": option_id, "label": label_en if english else label_ru}
                for option_id, label_ru, label_en in options
            ],
        }
        for question_id, prompt_ru, prompt_en, options, _answer in QUESTIONS
    ]


def grade_answers(answers: dict[str, str]) -> int:
    if set(answers) != {question[0] for question in QUESTIONS}:
        raise ValueError("All quiz questions must be answered")
    score = 0
    for question_id, _ru, _en, options, correct in QUESTIONS:
        chosen = answers[question_id]
        if chosen not in {option[0] for option in options}:
            raise ValueError("Unknown quiz option")
        score += chosen == correct
    return score
