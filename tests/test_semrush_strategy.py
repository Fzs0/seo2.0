from app.engine.semrush_strategy import _normalized_url, parse_semrush_strategy_rows, preview_semrush_strategy_rows
from app.services.keyword_service import prepare_stored_semrush_strategy_validation


def _item(cluster: str, keyword: str, urls: list[str], volume: int = 100) -> dict:
    return {
        "pageClusterId": cluster,
        "database": "us",
        "keyword": keyword,
        "page": cluster,
        "topic": "topic",
        "pageType": "Sub page",
        "intent": "Informational",
        "volume": volume,
        "kd": 20,
        "top10Urls": urls,
        "contentReferences": [],
    }


def test_strategy_builder_preserves_page_cluster_and_competitors():
    rows = parse_semrush_strategy_rows([
        [
            "Database", "Keyword", "Page", "Topic", "Page type", "Volume",
            "Keyword Difficulty", "Intent", "Content reference 1", "Competitor on TOP 10 #1",
        ],
        ["us", "best vape", "best vape", "vape", "Pillar page", "100", "20", "Informational, Commercial", "https://ref.test", "https://competitor.test"],
        ["us", "best pod vape", "best vape", "vape", "Pillar page", "200", "30", "Commercial", "", "https://competitor.test"],
        ["us", "vape battery", "vape battery", "vape", "Sub page", "50", "10", "Transactional", "", "https://battery.test"],
    ])

    assert rows[0]["page"] == "best vape"
    assert rows[0]["pageClusterId"] == rows[1]["pageClusterId"]
    assert rows[0]["pageClusterId"] != rows[2]["pageClusterId"]
    assert rows[0]["contentReferences"] == ["https://ref.test"]
    assert rows[0]["top10Urls"] == ["https://competitor.test"]

    preview = preview_semrush_strategy_rows(rows)
    assert preview["rowCount"] == 3
    assert preview["databases"] == ["us"]
    assert preview["topicCount"] == 1
    assert preview["pageClusterCount"] == 2
    assert preview["pageTypeCounts"] == {"Pillar page": 1, "Sub page": 1}
    assert preview["metrics"]["volumeSum"] == 350
    assert preview["metrics"]["top10Coverage"] == 1
    assert not preview["anomalies"]


def test_strategy_builder_flags_unsupported_page_type():
    rows = parse_semrush_strategy_rows([
        ["Database", "Keyword", "Page", "Topic", "Page type", "Volume", "Competitor on TOP 10 #1"],
        ["us", "best vape", "best vape", "vape", "Landing page", "100", "https://competitor.test"],
    ])

    preview = preview_semrush_strategy_rows(rows)
    assert [item["code"] for item in preview["anomalies"]] == ["unsupported_page_type"]


def test_strategy_builder_validates_cluster_boundaries_and_normalizes_urls():
    common = [f"https://example{i}.test/page" for i in range(1, 5)]
    rows = [
        _item("validated", "alpha", ["https://www.example1.test/page/?utm_source=x", *common[1:], *[f"https://a{i}.test" for i in range(6)]]),
        _item("validated", "beta", ["http://example1.test/page", *common[1:], *[f"https://b{i}.test" for i in range(6)]]),
        _item("missing", "gamma", ["https://only.test"]),
    ]

    preview = preview_semrush_strategy_rows(rows, {"version": "test-v1"})
    clusters = {item["id"]: item for item in preview["clusters"]}

    assert clusters["validated"]["validationStatus"] == "validated"
    assert clusters["missing"]["validationStatus"] == "split_review"
    assert preview["clusterValidation"] == {
        "ruleVersion": "test-v1",
        "counts": {"validated": 1, "split_review": 1},
        "aiReadyCount": 1,
        "reviewCount": 1,
    }
    assert _normalized_url("https://youtube.com/watch?v=one") != _normalized_url("https://youtube.com/watch?v=two")
    assert _normalized_url("https://www.example.test/page/?utm_source=x") == _normalized_url("http://example.test/page")


def test_stored_strategy_builder_batch_gets_validation_updates():
    common = [f"https://example{i}.test/page" for i in range(10)]
    records = [
        {
            "id": f"row-{index}",
            "keyword": keyword,
            "semrush_database": "us",
            "volume": 100 - index,
            "kd": 20,
            "intent": "commercial",
            "topic_cluster": "topic",
            "topic_cluster_id": "cluster-id",
            "page_group": "page",
            "page_type": "Sub page",
            "raw": {
                "_strategy_builder": {
                    "topic": "topic",
                    "page": "page",
                    "page_type": "Sub page",
                    "intent": "Commercial",
                    "top10_urls": common,
                    "content_references": [],
                }
            },
        }
        for index, keyword in enumerate(("alpha", "beta"))
    ]

    updates, preview = prepare_stored_semrush_strategy_validation(records, {"version": "test-v1"})

    assert preview["clusterValidation"]["counts"] == {"validated": 1}
    assert {item["cluster_role"] for item in updates} == {"pillar", "supporting"}
    assert all(item["pillar_keyword"] == "alpha" for item in updates)
    assert all(
        item["raw"]["_strategy_builder"]["cluster_validation"]["status"] == "validated"
        for item in updates
    )
