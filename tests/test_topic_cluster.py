from app.engine.topic_cluster import assign_topic_clusters


def test_clusters_related_queries_and_selects_pillar():
    rows = assign_topic_clusters(
        [
            {"keyword": "best vape flavors", "priority": "P1", "intent": "commercial", "volume": 1000, "kd": 20},
            {"keyword": "vape flavors 2025", "priority": "P2", "intent": "informational", "volume": 5000, "kd": 10},
            {"keyword": "best disposable vape", "priority": "P1", "intent": "commercial", "volume": 800, "kd": 25},
        ]
    )
    assert rows[0]["topicClusterId"] == rows[1]["topicClusterId"]
    assert rows[0]["clusterSize"] == 2
    assert rows[0]["clusterRole"] == "pillar"
    assert rows[0]["topicCluster"] == "best vape flavors"
    assert rows[2]["clusterRole"] == "standalone"


def test_seed_keyword_is_a_stronger_grouping_signal():
    rows = assign_topic_clusters(
        [
            {"keyword": "how to use vape", "seedKeyword": "vape guide", "priority": "P1"},
            {"keyword": "vape battery safety", "seedKeyword": "vape guide", "priority": "P2"},
        ]
    )
    assert rows[0]["topicClusterId"] == rows[1]["topicClusterId"]
    assert {row["clusterRole"] for row in rows} == {"pillar", "supporting"}
