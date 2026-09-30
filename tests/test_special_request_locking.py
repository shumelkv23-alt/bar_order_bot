from sqlalchemy.dialects import postgresql

from app.services.orders import _special_request_lock_statement


def test_special_request_lock_scopes_for_update_to_base_row() -> None:
    statement = _special_request_lock_statement(17)
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "LEFT OUTER JOIN menu_items" in sql
    assert sql.rstrip().endswith("FOR UPDATE OF special_requests")
