from pathlib import Path


def test_dashboard_analytics_template_present():
    text = Path("app/templates/dashboard.html").read_text(encoding="utf-8")
    assert "revenueChart" in text
    assert "paymentChart" in text
    assert "analytics-kpi-grid" in text
    assert "أفضل المنتجات مبيعًا" in text


def test_dashboard_analytics_queries_present():
    text = Path("app/routes.py").read_text(encoding="utf-8")
    assert "chart_values" in text
    assert "payment_breakdown" in text
    assert "top_products" in text
    assert "previous_revenue" in text
