from app.db_compat import translate_sql


def test_sum_boolean_comparisons_are_numeric_cases():
    sql = """SELECT COUNT(*) total,
        SUM(employment_status='active') active,
        SUM(employment_status='leave') on_leave
        FROM employees"""
    translated = translate_sql(sql)
    assert "SUM(CASE WHEN employment_status='active' THEN 1 ELSE 0 END)" in translated
    assert "SUM(CASE WHEN employment_status='leave' THEN 1 ELSE 0 END)" in translated


def test_normal_sums_and_existing_cases_are_unchanged():
    sql = "SELECT SUM(amount), SUM(CASE WHEN status='approved' THEN amount ELSE 0 END) FROM x"
    assert translate_sql(sql) == sql


def test_placeholder_comparison_is_supported():
    assert "SUM(CASE WHEN status=%s THEN 1 ELSE 0 END)" in translate_sql("SELECT SUM(status=?) FROM x")
