from app.engine.csv import normalize_imported_keywords


def test_trend_series_is_preserved_and_latest_value_is_indexed():
    rows = normalize_imported_keywords([
        ["Keyword", "Trend"],
        ["best vape", "0.12,0.29,1.00"],
    ])
    assert rows[0]["trend"] == 1.0
    assert rows[0]["trendData"] == [0.12, 0.29, 1.0]
