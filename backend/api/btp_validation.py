"""Deterministic comparison of BTP observations with simulated metrics."""
from __future__ import annotations

from math import isfinite, sqrt
from statistics import mean
from typing import Any


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _metric_bundle(pairs: list[tuple[float, float]]) -> dict[str, float | int | None]:
    if not pairs:
        return {"n": 0, "mae": None, "rmse": None, "mape_pct": None, "bias": None}

    errors = [simulated - observed for observed, simulated in pairs]
    absolute_errors = [abs(error) for error in errors]
    percentage_errors = [abs(error) / abs(observed) * 100 for (observed, _), error in zip(pairs, errors) if observed]
    return {
        "n": len(pairs),
        "mae": round(mean(absolute_errors), 4),
        "rmse": round(sqrt(mean(error * error for error in errors)), 4),
        "mape_pct": round(mean(percentage_errors), 4) if percentage_errors else None,
        "bias": round(mean(errors), 4),
    }


def validate_btp_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare paired BTP and simulation values without inventing missing data.

    Each record may contain speed and/or travel-time pairs using these fields:
    ``observed_speed_kmh``, ``simulated_speed_kmh``, ``observed_travel_time_s``,
    and ``simulated_travel_time_s``. Invalid or incomplete pairs are reported,
    not silently imputed.
    """
    speed_pairs: list[tuple[float, float]] = []
    travel_time_pairs: list[tuple[float, float]] = []
    excluded = 0
    for record in records:
        speed_observed = _finite(record.get("observed_speed_kmh"))
        speed_simulated = _finite(record.get("simulated_speed_kmh"))
        time_observed = _finite(record.get("observed_travel_time_s"))
        time_simulated = _finite(record.get("simulated_travel_time_s"))
        included = False
        if speed_observed is not None and speed_simulated is not None and speed_observed >= 0 and speed_simulated >= 0:
            speed_pairs.append((speed_observed, speed_simulated))
            included = True
        if time_observed is not None and time_simulated is not None and time_observed >= 0 and time_simulated >= 0:
            travel_time_pairs.append((time_observed, time_simulated))
            included = True
        if not included:
            excluded += 1

    return {
        "status": "complete" if speed_pairs or travel_time_pairs else "no_pairs",
        "method": "paired_observation_comparison",
        "record_count": len(records),
        "excluded_record_count": excluded,
        "metrics": {
            "speed_kmh": _metric_bundle(speed_pairs),
            "travel_time_s": _metric_bundle(travel_time_pairs),
        },
    }