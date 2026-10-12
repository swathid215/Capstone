# BTP Validation Protocol

## Purpose

This protocol defines how Bengaluru Traffic Police (BTP) observations are compared with UIDT simulation outputs without imputing missing values or treating a simulation result as observed truth.

## Required paired-record schema

CSV and JSON inputs use these fields:

```text
timestamp, corridor_id, observed_speed_kmh, simulated_speed_kmh,
observed_travel_time_s, simulated_travel_time_s
```

`timestamp` and `corridor_id` are provenance fields. At least one complete speed pair or travel-time pair is required for a record to contribute to a metric.

## Metrics

For each metric, UIDT reports:

- `n`: number of complete observation/simulation pairs;
- MAE: mean absolute error;
- RMSE: root mean square error;
- MAPE: mean absolute percentage error, excluding zero observed denominators;
- bias: mean(simulated - observed).

Records with missing, non-numeric, or negative paired values are excluded and counted. No interpolation, zero filling, or state-based substitution is performed.

## Required analysis procedure

1. Document the BTP data source, collection period, sensor/corridor identifiers, units, timezone, and access restrictions.
2. Map BTP records to the same corridor and evaluation window used by the SUMO run.
3. Freeze the network, route files, SUMO version, random seed, demand file, and evaluation window before comparison.
4. Report results separately for speed and travel time, including sample count and excluded records.
5. Use a held-out time period or corridor for final validation where the dataset permits it.
6. Report confidence intervals or bootstrap intervals when the number of paired observations is sufficient.
7. Do not describe the workflow as calibrated or validated unless the mapping, sample coverage, and error thresholds are pre-specified.

## API and UI

The API endpoint is `POST /api/validation/btp` with a JSON body containing `records`. The Traffic Report view provides the same workflow through the `Upload CSV/JSON` control. The panel reports `no_pairs` rather than displaying fabricated validation values when the input is incomplete.

## Submission boundary

Until BTP records are mapped to the SUMO corridor and the paired analysis is frozen, manuscript language should say **BTP validation protocol implemented** or **external validation pending**, not **validated digital twin**.
