from api.btp_validation import validate_btp_records


def test_btp_validation_reports_deterministic_metrics_and_exclusions():
    result = validate_btp_records([
        {
            "timestamp": "2026-06-13T19:00:00Z",
            "observed_speed_kmh": 30,
            "simulated_speed_kmh": 27,
            "observed_travel_time_s": 120,
            "simulated_travel_time_s": 132,
        },
        {"timestamp": "missing-pair"},
    ])

    assert result["status"] == "complete"
    assert result["record_count"] == 2
    assert result["excluded_record_count"] == 1
    assert result["metrics"]["speed_kmh"] == {
        "n": 1,
        "mae": 3.0,
        "rmse": 3.0,
        "mape_pct": 10.0,
        "bias": -3.0,
    }
    assert result["metrics"]["travel_time_s"]["mape_pct"] == 10.0


def test_btp_validation_does_not_claim_validation_without_pairs():
    result = validate_btp_records([{"observed_speed_kmh": 30}])

    assert result["status"] == "no_pairs"
    assert result["metrics"]["speed_kmh"]["n"] == 0