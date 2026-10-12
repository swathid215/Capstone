# UIDT State-Conditioning Data Lineage Diagram

## Current Implementation (Tasks 39-44)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL REAL-WORLD AUTHORITY                                                  │
│ ─────────────────────────────────────────────────────────────────────────────  │
│ TomTom Traffic State Observations (161 historical captures, June 2026)         │
│ • current_speed (km/h)                                                         │
│ • free_flow_speed (km/h)                                                       │
│ • current_travel_time (seconds)                                                │
│ • free_flow_travel_time (seconds)                                              │
│ • timestamp (ISO 8601)                                                         │
│ • incident_count                                                               │
│ • road_closure_flag                                                            │
└────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ↓ (DERIVATION, DETERMINISTIC)
┌────────────────────────────────────────────────────────────────────────────────┐
│ DERIVED NORMALIZED STATE LAYER (Task 35 → Task 39)                             │
│ ─────────────────────────────────────────────────────────────────────────────  │
│ Preserved across all downstream tasks:                                         │
│                                                                                 │
│ • speed_ratio = current_speed / free_flow_speed                                │
│ • speed_loss = 1 - speed_ratio                                                 │
│ • travel_time_ratio = current_travel_time / free_flow_travel_time              │
│ • delay_ratio = travel_time_ratio - 1                                          │
│                                                                                 │
│ • congestion_state ∈ {FREE_FLOW, MODERATE, CONGESTED, SEVERE}                 │
│   (via Task 35 quartile binning; reproducible training/validation split)       │
│                                                                                 │
│ • incident_state ∈ {INCIDENT, NO_INCIDENT}                                    │
│ • source = "TOMTOM"                                                            │
│ • confidence = 1.0 (all derived from raw provider returns)                    │
└────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ↓ (STATE SELECTION, DETERMINISTIC LOOKUP)
┌────────────────────────────────────────────────────────────────────────────────┐
│ TRAFFIC TWIN STATE SERVICE (Task 40, backend/api/traffic_twin_service.py)     │
│ ─────────────────────────────────────────────────────────────────────────────  │
│ • GET /traffic-state/timeseries → returns all 161 states                       │
│ • GET /traffic-state/latest → returns most recent state                        │
│ • GET /traffic-state/{timestamp} → exact timestamp lookup or 404               │
│ • No interpolation                                                              │
│ • No padding or fallback selection                                             │
│ • Read-only; no state modification                                             │
│                                                                                 │
│ ✓ Timestamp = "2026-06-13T19:00:03.593181" (example)                          │
│ ✓ Returns: speed_ratio=0.694, state="SEVERE", incident_count=14               │
│ ✗ Does NOT return: SUMO vehicle count, SUMO demand, SUMO parameters           │
│ ✗ Does NOT accept: SUMO configuration input                                    │
└────────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┴────────────────────┐
                    │                                      │
                    ↓ (UI OBSERVATION PANEL)              ↓ (SIMULATION CONTEXT)
        ┌──────────────────────────┐           ┌──────────────────────────┐
        │ COMMAND CENTER PANEL     │           │ FIXED SUMO BASELINE      │
        │ (TrafficTwinPanel.jsx)   │           │ (UNCHANGED BY STATE)     │
        │                          │           │                          │
        │ OBSERVED (TomTom):       │           │ • network:               │
        │ ├─ current_speed         │           │   auto_tool/             │
        │ ├─ free_flow_speed       │           │   cleaned_network.net    │
        │ └─ incident_count        │           │                          │
        │                          │           │ • demand:                │
        │ DERIVED:                 │           │   auto_tool/             │
        │ ├─ speed_ratio           │           │   simulation.rou.xml     │
        │ ├─ congestion_state      │           │   (5,450 vehicles)       │
        │ └─ timestamp             │           │                          │
        │                          │           │ • routes:                │
        │ Display: "What was the   │           │   1, 2, 3, 4 (existing) │
        │ real world doing?"       │           │                          │
        │                          │           │ • signals:               │
        │ ✓ Read-only              │           │   native SUMO            │
        │ ✓ No user editing        │           │                          │
        │ ✓ No SUMO injection      │           │ ✓ FIXED FOR ALL STATES   │
        │ ✓ Labeled lineage        │           │ ✓ IDENTICAL FOR FREE...  │
        │                          │           │   ...FLOW/MODERATE/etc   │
        │                          │           │ ✓ No state-dependent     │
        │                          │           │   modification           │
        └──────────────────────────┘           └──────────────────────────┘
```

---

## Intervention Simulation Path (Tasks 41-43)

```
SELECTED TOMTOM STATE (from panel)
│
└─→ SUMO BASELINE RUN (Fixed)
    │
    ├─ Network:     auto_tool/cleaned_network.net.xml (unchanged)
    ├─ Demand:      5,450 vehicles (unchanged)
    ├─ Route:       Baseline (routes 1-3 only)
    ├─ Adoption:    0% (all vehicles on routes 1-3)
    └─→ Outputs:    baseline_travel_time, baseline_speed, baseline_throughput
                    (SUMO result, NOT TomTom validation)
    
    vs.
    
└─→ SUMO INTERVENTION RUN (Fixed intervention logic, adoption-variable)
    │
    ├─ Network:     auto_tool/cleaned_network.net.xml (unchanged)
    ├─ Demand:      5,450 vehicles (unchanged)
    ├─ Route:       Route 4 (alternative) + routes 1-3
    ├─ Adoption:    75% adoption (variable by experiment)
    │               └─ Percentage of demand diverted to Route 4
    └─→ Outputs:    intervention_travel_time, intervention_speed, intervention_throughput
                    (SUMO result, NOT TomTom validation)

COMPARISON
│
└─→ Improvement = (baseline - intervention) / baseline × 100%
    │
    ├─ Travel time reduction: (246.04 - 194.78) / 246.04 = 20.8% ✓ (SEVERE state example)
    ├─ Speed improvement:     (40.90 - 24.91) / 24.91 = 64.2% ✓ (SEVERE state example)
    │
    └─→ CRITICAL: This is SUMO-vs-SUMO comparison
                  TomTom state provides the HISTORICAL CONTEXT
                  (i.e., "we ran this on June 13 at 19:00")
                  
                  TomTom does NOT:
                  ✗ Validate SUMO numbers
                  ✗ Provide demand counts
                  ✗ Provide behavioral parameters
                  ✗ Condition SUMO in any physical way
```

---

## Validation Status by Task

### Task 28: Demand Sensitivity (Foundational Gate)

```
Hypothesis: TomTom speed_ratio ∝ SUMO demand multiplier

Tested:    low (0.5x) → baseline (1.0x) → high (1.5x)

Result:    HIGH DEMAND (1.5x) ≠ LOWER SPEED
           Expected: speed↓ as demand↑
           Observed: speed(1.5x)=34.97 km/h > speed(1.0x)=30.87 km/h
           
           Response is NON-MONOTONIC
           
Conclusion: GATE FAILED
            "A demand multiplier cannot be estimated defensibly from these three runs."
            
Evidence:  docs/task28_tomtom_demand_model/demand_sensitivity.csv
           docs/task28_tomtom_demand_model/TASK28_REPORT.md (Section 7)
```

### Task 36: Control Surface Audit

```
Hypothesis: Some SUMO control (demand, speed, car-following, signals, ...) 
            can be identified from TomTom observations

Evaluated:  11 SUMO control categories
            ├─ Demand (vehicle count)
            ├─ Demand (OD matrix)
            ├─ Desired speed
            ├─ Car-following (tau)
            ├─ Acceleration/deceleration
            ├─ Minimum gap
            ├─ Lane-changing behavior
            ├─ Signal timing
            ├─ Edge speed limits
            ├─ Routing logic
            └─ Insertion timing

Evidence:   TomTom fields examined:
            • current_speed, free_flow_speed
            • current_travel_time, free_flow_travel_time
            • incident_count
            
            Missing from TomTom:
            ✗ Vehicle counts
            ✗ OD demand
            ✗ Detector phases or signals
            ✗ Per-edge measurements
            ✗ Lane-level occupancy
            ✗ Behavioral trajectory data
            
Conclusion: NO IDENTIFIABLE MAPPING
            "TomTom provides state observations, not driver behavior, 
             counts, OD demand, lane occupancy, signal timing, or 
             per-edge measurements."
            
Evidence:  docs/task36_traffic_state_twin/sumo_control_surface.csv
           docs/task36_traffic_state_twin/TASK36_REPORT.md (Sections 2-3)
```

### Task 38: Formal Parameterization (Critical Gate)

```
Task: Assign each state (FREE_FLOW, MODERATE, CONGESTED, SEVERE) 
      to a SUMO parameter value

Result:

State       | Parameter      | Baseline   | State Value | Reason              | Confidence
─────────────────────────────────────────────────────────────────────────────────────
FREE_FLOW   | none           | NOT_ID     | NOT_ID      | No TomTom evidence  | HIGH
MODERATE    | none           | NOT_ID     | NOT_ID      | No TomTom evidence  | HIGH
CONGESTED   | none           | NOT_ID     | NOT_ID      | No TomTom evidence  | HIGH
SEVERE      | none           | NOT_ID     | NOT_ID      | No TomTom evidence  | HIGH

Decision: Do not run state-conditioned SUMO experiments.
          "No parameter is identified, no physical state-conditioned run 
           is scientifically justified in Task38."

Evidence: docs/task38_state_conditioned_sumo/state_parameterization.csv
          docs/task38_state_conditioned_sumo/TASK38_REPORT.md (Sections 3, 7)
```

### Task 38: Replay Mapping (What Was Actually Implemented)

```
Scenario  | State    | SUMO Param Mapping | Simulation Context | Physical Repro? | Type
───────────────────────────────────────────────────────────────────────────────────
free_flow | FREE_FLOW | none              | BASELINE_SUMO      | NO             | STATE_SELECTION_ONLY
moderate  | MODERATE  | none              | BASELINE_SUMO      | NO             | STATE_SELECTION_ONLY
congested | CONGESTED | none              | BASELINE_SUMO      | NO             | STATE_SELECTION_ONLY
severe    | SEVERE    | none              | BASELINE_SUMO      | NO             | STATE_SELECTION_ONLY

Implementation:   Each state is a read-only label for a TomTom observation.
                  Underlying SUMO baseline is FIXED and identical for all states.

Evidence:        docs/task38_state_conditioned_sumo/state_replay_mapping.csv
```

### Task 39-44: Integration Maintained Boundary

```
Task 39: TrafficTwinService created (read-only observation layer)
         ✓ No SUMO parameter injection
         ✓ Deterministic timestamp lookup
         ✓ Preserves OBSERVED/DERIVED/SIMULATED lineage

Task 40: TrafficTwinPanel UI created
         ✓ Explicit labels: "TomTom speed" vs. "SUMO speed"
         ✓ Read-only observation display
         ✓ No user ability to modify state or create demand

Task 41: Demonstration (one state: SEVERE)
         ✓ Baseline vs. intervention comparison works
         ✓ Adoption-only intervention (75%)
         ✓ No state-conditioned parameter change
         ✓ Explicit exclusions of invalid claims

Task 42: Workflow documentation
         ✓ Confirms no network/route/demand changes

Task 43: Multi-state demo (four states selected)
         ✓ FREE_FLOW: EXECUTED
         ✓ MODERATE:  NOT_RUN (resource constraints)
         ✓ CONGESTED: NOT_RUN (resource constraints)
         ✓ SEVERE:    EXECUTED
         ✓ Status explicitly marked; no fake results

Task 44: Production hardening
         ✓ API validation: all endpoints work correctly
         ✓ Lineage enforcement: OBSERVED/DERIVED/SIMULATED properly labeled
         ✓ No state-conditioned demand injection confirmed
         ✓ No physical parameter modification confirmed
         
         Hardening issues (does not affect scientific boundary):
         • No automated test suite
         • Machine-specific data path
         • Artifact-based run records (not persistent DB)
         • Pre-existing build warnings

Evidence: docs/task39_traffic_twin_integration/traffic_twin_data_lineage.csv
          (ALL entries show demand_context="BASELINE_SUMO", 
                              simulation_context="PEAK_BASELINE")
```

### Task 45-46: Defect Found in Four-State Results

```
Claimed: Four-state intervention experiment completed
         ├─ FREE_FLOW:  240.62s → 179.29s  (−25.5%)
         ├─ MODERATE:   250.56s → 187.03s  (−25.5%)
         ├─ CONGESTED:  250.56s → 187.03s  (−25.5%)  ← IDENTICAL
         └─ SEVERE:     246.04s → 194.78s  (−20.8%)

Audit Finding: MODERATE and CONGESTED have identical results
               (down to the second)

Root Cause:  Execution script used same SUMO context for both states
             ├─ Same start_time, end_time
             ├─ Same adoption_rate_pct
             ├─ Same origin, destination
             ├─ Same baseline route file
             └─ Same intervention logic
             
             No state-conditioned demand branch exists
             No SUMO parameters change per state
             
             Therefore: MODERATE and CONGESTED ran the SAME simulation
                       under different labels
                       
             This is NOT evidence of four-state physical conditioning

Conclusion:  Four-state experiment is not valid as claimed
             ✓ FREE_FLOW:  one independent SUMO run
             ✓ SEVERE:     one independent SUMO run
             ✗ MODERATE:   same run as CONGESTED (mislabeled)
             ✗ CONGESTED:  same run as MODERATE (mislabeled)
             
             Implementation type: STATE_SELECTION_ONLY (not physical state-conditioning)
             
             Correction: Mark MODERATE/CONGESTED as NOT_RUN
                        Update results table to show only 2 executed, 2 not run

Evidence:    docs/task46_results_validity/TASK46_REPORT.md (Sections 1-4)
             docs/task46_results_validity/results_validity_audit.csv
             docs/task45_results_experiments/._task45_runs.py (execution code)
             docs/task45_results_experiments/four_state_intervention_results.csv (identical rows)
             backend/api/simulation_service.py (single baseline context for all states)
```

---

## Classification Summary

| Task | Classification | Evidence of Defensibility |
|---|---|---|
| **28** | C - Signal identified, mapping NOT validated | Non-monotonic demand response; gate failed |
| **36** | C - State layer built, NO identifiable SUMO control | All 11 SUMO controls not identified by TomTom |
| **38** | C - NO identifiable SUMO control; correct decision to stop | All 4 states: NOT_IDENTIFIABLE |
| **38 Implementation** | **C - STATE_SELECTION_ONLY** | all states → BASELINE_SUMO; physical_reproduction=NO |
| **39-44** | **B - DATA-DRIVEN OBSERVATION + REPLAY INTEGRATED** | Lineage enforced; boundary maintained |
| **45-46** | **DEFECT: MODERATE=CONGESTED (same simulation, different labels)** | Audit stop; no new experiment; corrected in Task 46 |
| **FINAL** | **(C) STATE SELECTION/CONTEXT ONLY** | Correct implementation; now with documented defect correction |

---

## Minimum Experiments to Upgrade to (A)

**Do not attempt any of the following until Experiments 1-2 pass.**

### Experiment 1: Demand Monotonicity Validation

**Must pass before proceeding.**

Objective: Prove causal demand-to-state relationship with monotonic SUMO response

Test: Run SUMO at 0.5x, 0.75x, 1.0x, 1.25x, 1.5x demand
      Measure: corridor speed must monotonically decrease as demand increases (or delay increases)
      
Success Criterion: Monotonic response within ±5% of observed TomTom metrics; reproducible across 5 runs

Failure Handling: If non-monotonic (like Task 28): STOP. Fix SUMO model first.

**Evidence Artifact:** demand_response_validation.csv

---

### Experiment 2: State-to-Demand Mapping via Independent Detector Data

**Must pass before proceeding to Experiment 3-4.**

Objective: Identify unique SUMO demand multiplier for each TomTom state

Test: Acquire or deploy loop/radar detector for corridor
      Correlate detector vehicle count to TomTom speed_ratio
      Run SUMO with detector-derived demand
      Compare SUMO metrics to real-world observations
      
Success Criterion: State-to-demand mapping unique and reversible; MAPE ≤ 10% on training data

Failure Handling: If mapping non-unique or error >10%: STOP. Collect more data or external factors confound relationship.

**Evidence Artifact:** state_demand_mapping_table.csv (e.g., FREE_FLOW→0.5x, MODERATE→0.9x, SEVERE→1.5x)

---

### Experiment 3: Behavioral Parameter Sensitivity (Optional)

**Only if Experiment 2 succeeds AND demand alone explains <95% of variance.**

Objective: Determine whether behavioral changes (desired speed, car-following, lane-changing) also affect state

Test: Fix demand (from Experiment 2); vary car-following tau, desired speed, lcSigma
      Measure: speed, delay, occupancy sensitivity to behavioral parameters
      
Success Criterion: Demand accounts for ≥95% of speed_ratio variance; behavioral changes <5%

Failure Handling: If behavioral changes >10%: More complex model needed; require trajectory/detector phase data.

**Evidence Artifact:** behavioral_sensitivity_analysis.csv

---

### Experiment 4: Held-Out Validation on Unseen Time Periods

**Only if Experiments 1-3 pass.**

Objective: Prove state-to-demand mapping generalizes to new data

Test: Split TomTom data: training (June 11-15) vs. validation (June 16-19)
      Fit mapping on training
      Run SUMO with predicted demand on validation
      Measure prediction error
      
Success Criterion: MAPE ≤ 15% for travel time; MAPE ≤ 10% for speed; no systematic bias

Failure Handling: If MAPE > 15%: Mapping does not generalize; external factors (incidents, special events) dominate.

**Evidence Artifact:** held_out_validation_results.csv

---

### Experiment 5: Multi-State Intervention Generalization (Production Readiness)

**Only if Experiments 1-4 pass.**

Objective: Prove intervention benefits hold across all four states

Test: Run full baseline/intervention pairs for FREE_FLOW, MODERATE, CONGESTED, SEVERE
      Each pair uses state-specific demand from Experiment 2
      Measure: intervention benefit (% travel time reduction, % speed improvement)
      
Success Criterion: Intervention benefit positive and consistent across all states
                   OR monotonic in demand level (predictable variation)
                   
Failure Handling: If negative benefit in any state: Intervention capacity exceeded; use different mechanism for that state.

**Evidence Artifact:** multi_state_intervention_results.csv (all four states with distinct SUMO contexts)

---

## Current Status: Do Not Implement Option (A)

✓ Implementation is correct as **(C) State Selection/Context Only**
✓ Lineage is properly enforced (OBSERVED/DERIVED/SIMULATED)
✓ Defect in Task 45-46 is documented and stopped at audit
✗ No evidence exists for state-to-SUMO mapping (Tasks 28, 36, 38)
✗ Experiments 1-2 not yet run
✗ Independent demand data not acquired

**Recommendation:** Maintain current implementation. Run Experiments 1-2 (3-6 months if funded). Revisit upgrade to (A) only after passing validation gates.
