from api import demand_service


def test_demand_service_returns_phase1_matrix_and_sumo_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(demand_service, "DEMAND_CACHE", tmp_path / "integration_demand.json")

    result = demand_service.get_or_generate_demand(
        target_hour=9,
        ward_no=163,
        congestion_multiplier=1.0,
        refresh=True,
    )

    assert result["demand_matrix"]
    assert result["scenario_metadata"]["source"].startswith("Phase 1 ward CSV")
    assert result["scenario_metadata"]["movement_id"] == 145
    assert result["route_file"] == str(demand_service.ROUTE_FILE)
    assert result["vehicle_count_in_target_hour"] >= 0