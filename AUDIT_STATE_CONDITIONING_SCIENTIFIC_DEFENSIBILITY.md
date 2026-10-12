# UIDT Tasks 28-46 Audit: Scientific Defensibility of SUMO Physical State-Conditioning on TrafficTwinState

**Audit Date:** 2026-08-18  
**Scope:** Tasks 28-46, read-only investigation only  
**Auditor:** Evidence-based classification review  
**Status:** AUDIT COMPLETED WITH DEFECT REPORT

---

## EXECUTIVE SUMMARY

**CRITICAL FINDING: No scientifically defensible evidence exists to implement physically state-conditioned SUMO on TrafficTwinState.**

The audit reviewed 19 sequential experimental and integration tasks (Tasks 28-46) and examined:
- Exact TomTom-to-SUMO mappings proposed and tested
- Experimental results and validation status
- Current production implementation
- Lineage enforcement and claims boundaries

**Final determination:** Implementation is correctly classified as **(C) State Selection/Context Only**, not (A) physically state-conditioned SUMO. The four traffic states (FREE_FLOW, MODERATE, CONGESTED, SEVERE) have been **NOT_IDENTIFIABLE** to any SUMO parameter since Task 38, and this boundary has been maintained through Tasks 39-46.

**However, a defect was found in Tasks 45-46:** The multi-state intervention results file claims to show four distinct state-conditioned simulations, but an audit of the execution code and results data revealed that MODERATE and CONGESTED used identical SUMO contexts and produced identical numerical results. This is not a physical state-conditioning experiment; it is a labeling error in state context documentation.

---

## PART 1: EVIDENCE CHAIN FOR STATE-TO-SUMO MAPPINGS

### 1.1 Task 28: TomTom-Driven Demand Model (Foundational)

**Classification:** C - Data-driven demand signal identified but demand mapping NOT validated

**Hypothesis Tested:**  
Can TomTom traffic state observations be mapped to a valid SUMO demand multiplier?

**Experimental Design:**
- TomTom normalized signals: `speed_ratio`, `speed_loss`, `travel_time_ratio`, `travel_time_increase`
- Three SUMO demand scenarios: low (2,725 veh), baseline (5,450 veh), high (8,175 veh)
- Overlap edge corridor: 6 edges, 3 km study section
- Native SUMO edgeData measurements: speed, travel time, occupancy, flow

**Critical Result:**

| Scenario | Vehicles | Corridor speed ratio | Corridor delay |
|---|---:|---:|---:|
| Low demand | 2,725 | 0.922 | 755.6 s |
| Baseline | 5,450 | 0.746 | 98,952.8 s |
| High demand | 8,175 | 0.845 | 85,444.8 s |

**Finding:** Response is **NON-MONOTONIC**
- High demand (8,175 veh) produced HIGHER corridor speed than baseline (5,450 veh): 34.97 km/h vs 30.87 km/h
- More demand did not produce consistently lower speed or higher congestion
- This violates the fundamental assumption needed to map TomTom state to demand

**Evidence Artifact:**  
[docs/task28_tomtom_demand_model/demand_sensitivity.csv](docs/task28_tomtom_demand_model/demand_sensitivity.csv)  
[docs/task28_tomtom_demand_model/TASK28_REPORT.md](docs/task28_tomtom_demand_model/TASK28_REPORT.md) - Section 7

**Conclusion:**  
"The causal gate failed. A demand multiplier cannot be estimated defensibly from these three runs."  
No TomTom-to-demand mapping attempted. Task 29 was required to diagnose before proceeding.

---

### 1.2 Task 36: Traffic-State Twin Model (State Observation Layer)

**Classification:** C - Traffic-State → SUMO mapping NOT identifiable

**What Was Built:**
- Normalized TomTom state layer with four classification states
- Task 35 state representation (preserved across subsequent tasks):
  ```
  speed_ratio = currentSpeed / freeFlowSpeed
  speed_loss = 1 - speed_ratio
  travel_time_ratio = currentTravelTime / freeFlowTravelTime
  delay_ratio = travel_time_ratio - 1
  congestion_state ∈ {FREE_FLOW, MODERATE, CONGESTED, SEVERE}
  ```

**State Mapping Analysis:**

| SUMO Control Surface | TomTom Evidence | Identifiable? |
|---|---|---|
| Demand (vehicle count) | None - TomTom has no counts | NO |
| Demand (OD matrix) | None - TomTom has no OD | NO |
| Vehicle desired speed | Speed ratio ≠ desired speed parameter | NO |
| Car-following tau | No behavioral observations | NO |
| Acceleration/deceleration | No per-vehicle observations | NO |
| Minimum gap | No lane-level data | NO |
| Signal timing | No detector phases | NO |
| Edge speed limits | No per-edge TomTom mapping | NO |
| Route assignment | No OD or route prediction | NO |

**Evidence Artifact:**  
[docs/task36_traffic_state_twin/sumo_control_surface.csv](docs/task36_traffic_state_twin/sumo_control_surface.csv)  
[docs/task36_traffic_state_twin/TASK36_REPORT.md](docs/task36_traffic_state_twin/TASK36_REPORT.md) - Section 2

**Conclusion:**  
"A reproducible TomTom traffic-state layer was built. A legitimate mapping from that state to SUMO demand or behavioral parameters could not be established from the available evidence without inventing counts, OD demand, or unobserved behavioral parameters."

---

### 1.3 Task 38: State-Conditioned SUMO Parameter Audit (Critical Gate)

**Classification:** C - TomTom state model built; NO identifiable SUMO control found

**Parameterization Results:**

| State | Parameter Mapping | Baseline Value | State-Conditioned Value | Physical Reason | Confidence |
|---|---|---|---|---|---|
| FREE_FLOW | `none` | NOT_IDENTIFIABLE | NOT_IDENTIFIABLE | No TomTom-identified SUMO parameter | high |
| MODERATE | `none` | NOT_IDENTIFIABLE | NOT_IDENTIFIABLE | No TomTom-identified SUMO parameter | high |
| CONGESTED | `none` | NOT_IDENTIFIABLE | NOT_IDENTIFIABLE | No TomTom-identified SUMO parameter | high |
| SEVERE | `none` | NOT_IDENTIFIABLE | NOT_IDENTIFIABLE | No TomTom-identified SUMO parameter | high |

**Evidence Artifact:**  
[docs/task38_state_conditioned_sumo/state_parameterization.csv](docs/task38_state_conditioned_sumo/state_parameterization.csv)  
[docs/task38_state_conditioned_sumo/TASK38_REPORT.md](docs/task38_state_conditioned_sumo/TASK38_REPORT.md) - Section 3

**Critical Decision:**  
"Running four scenarios with arbitrary behavioral parameters would create an artificial result and confuse 'SUMO responds to a changed parameter' with 'TomTom state is physically represented.' Since no parameter is identified, no physical state-conditioned run is scientifically justified in Task38."

**Result:** No SUMO runs were performed. Training/validation files explicitly record `NOT_PERFORMED` rather than fabricated metrics.

---

### 1.4 Task 38: Replay Mapping (What Was Actually Done)

**State-to-SUMO Implementation:**

| Replay Scenario | Source State | TomTom State | SUMO Parameter Mapping | Simulation Context | Physical Reproduction? | Implementation Type |
|---|---|---|---|---|---|---|
| free_flow | Task37 replay_01 | FREE_FLOW | `none` | BASELINE_SUMO | NO | STATE_SELECTION_ONLY |
| moderate | Task37 replay_02 | MODERATE | `none` | BASELINE_SUMO | NO | STATE_SELECTION_ONLY |
| congested | Task37 replay_03 | CONGESTED | `none` | BASELINE_SUMO | NO | STATE_SELECTION_ONLY |
| severe | Task37 replay_04 | SEVERE | `none` | BASELINE_SUMO | NO | STATE_SELECTION_ONLY |

**Evidence Artifact:**  
[docs/task38_state_conditioned_sumo/state_replay_mapping.csv](docs/task38_state_conditioned_sumo/state_replay_mapping.csv)

**Implementation Classification:** **Type (C) - State Selection/Context Only**

All four states map to the same fixed SUMO baseline context. The state label selects which historical observation is displayed, but the underlying SUMO demand, network, and route file remain unchanged across all states.

---

## PART 2: IMPLEMENTATION BOUNDARY (Tasks 39-44)

### 2.1 Task 39-40: Integration as Observation-Only Service

**Classification:** B - Data-driven observation + replay integrated; SUMO intervention PENDING

**Explicit Data Lineage:**

```
TOMTOM (OBSERVED)
  ↓ TomTom raw JSON: current_speed, free_flow_speed, current_travel_time, free_flow_travel_time
  ↓
TRAFFIC_TWIN_STATE (DERIVED)
  ↓ calculated: speed_ratio, speed_loss, travel_time_ratio, delay_ratio, congestion_state
  ↓
SUMO CONTEXT (SIMULATED)
  ↓ fixed baseline: demand, network, route, signals
  ↓
INTERVENTION (SIMULATED)
  ↓ fixed adoption-only route intervention: 0-100% adoption rate
```

**Evidence Artifact:**  
[docs/task39_traffic_twin_integration/traffic_twin_data_lineage.csv](docs/task39_traffic_twin_integration/traffic_twin_data_lineage.csv)  
[docs/task40_uidt_integration/TASK40_REPORT.md](docs/task40_uidt_integration/TASK40_REPORT.md) - Section 5

**Enforcement Mechanism:**
- Backend service: `traffic_twin_service.py` (read-only state queries)
- Frontend panel: `TrafficTwinPanel.jsx` (explicit OBSERVED/DERIVED/SIMULATED labels)
- API contract: Exact timestamp lookup, no interpolation, no demand injection
- Simulation service: Unchanged; reuses existing SUMO baseline and adoption logic

**Critical Statement from Task 40:**
> "TomTom observations are not converted into demand, vehicle counts, OD, speedFactor, or behavioral parameters. The selected state is scenario context only."

---

### 2.2 Task 41: First Demonstration (Validation)

**Classification:** B - Data-driven observation + replay integrated; SUMO intervention demonstrated

**Demonstration Scenario:**
- Selected real TomTom observation: `2026-06-13T19:00:03.593181` (SEVERE state)
- Workflow: TomTom observation → TrafficTwinState → UIDT scenario context → existing SUMO baseline/intervention
- Intervention mechanism: Existing adoption-only route intervention (75% adoption)

**SUMO Results (BASELINE vs. INTERVENTION only):**

| Metric | Baseline | Intervention | Change |
|---|---:|---:|---:|
| Mean travel time | 246.04 sec | 194.78 sec | -51.26 sec (-20.83%) |
| Mean speed | 24.91 km/h | 40.90 km/h | +15.99 km/h (+64.19%) |
| Actual adoption | 0% | 74.9% | N/A |

**Critical Boundary:**

Invalid claims explicitly excluded:
- "SUMO reproduces the TomTom state" — **NO**
- "TomTom calibrated SUMO" — **NO**
- "TomTom generated SUMO demand" — **NO**
- "The intervention improvement occurred in the real world" — **NO**
- "The SUMO result is numerically validated against TomTom" — **NOT_PERFORMED**

**Valid claim:**
> "For a historical real-world traffic state observed by TomTom, UIDT can attach that state as context and run an existing SUMO baseline/intervention comparison."

**Evidence Artifact:**  
[docs/task41_end_to_end_intervention/TASK41_REPORT.md](docs/task41_end_to_end_intervention/TASK41_REPORT.md) - Section 4

---

### 2.3 Task 43: Multi-State Evaluation (Partial)

**Classification:** B - Multi-state observation + intervention evaluation partially executed

**States Selected and Results:**

| State | TomTom Timestamp | Baseline Time | Intervention Time | Change | Status |
|---|---|---:|---:|---:|---|
| FREE_FLOW | 2026-06-12T22:00:02.778515 | 240.62 sec | 179.29 sec | -25.5% | EXECUTED |
| MODERATE | exact real state selected | — | — | — | NOT_RUN |
| CONGESTED | exact real state selected | — | — | — | NOT_RUN |
| SEVERE | 2026-06-11T19:11:52.043181 | 246.04 sec | 194.78 sec | -20.8% | EXECUTED |

**Implementation Mechanism:** Same existing adoption-only route intervention

**Evidence Artifact:**  
[docs/task43_multi_state_intervention/TASK43_REPORT.md](docs/task43_multi_state_intervention/TASK43_REPORT.md)

**Explicit Status:**  
Only FREE_FLOW and SEVERE were fully executed. MODERATE and CONGESTED remain selectable but marked as `NOT_RUN`. No fake results were created.

---

### 2.4 Task 44: Production Hardening

**Classification:** B - Integration complete but hardening issue remains

**API Validation Results:**
- `/health`: ✓ 200
- `/traffic-state/latest`: ✓ 200, 161 states
- `/traffic-state/{timestamp}`: ✓ 200 (exact match), ✓ 404 (invalid/empty)
- Lineage enforcement: OBSERVED/DERIVED/SIMULATED properly labeled
- No state-conditioned demand injection: ✓ Confirmed
- No physical parameter modification: ✓ Confirmed

**Hardening Issues Identified:**
1. No automated API test suite
2. Default TomTom data path is machine-specific
3. Run records are artifact-based (not persistent DB)
4. Only 2 of 4 multi-state intervention pairs fully executed
5. Pre-existing app build warnings (Leaflet, chunk size)
6. SUMO failure/incomplete-output paths not exercised

**Conclusion:** "The core user workflow is integrated and reproducible for exact historical state selection and existing SUMO baseline/intervention artifacts. Additional hardening is required before calling it production-ready for unattended operations."

**Evidence Artifact:**  
[docs/task44_production_validation/TASK44_REPORT.md](docs/task44_production_validation/TASK44_REPORT.md)

---

## PART 3: DEFECT DETECTED IN TASKS 45-46

### 3.1 Task 45: Multi-State Intervention Results

**What Was Claimed:**
Four-state intervention experiment with results for FREE_FLOW, MODERATE, CONGESTED, and SEVERE.

**Results Table (from Task 45):**

| Observed State Context | Baseline Time | Intervention Time | Change | Baseline Speed | Intervention Speed |
|---|---:|---:|---:|---:|---:|
| FREE_FLOW | 240.62 sec | 179.29 sec | -25.5% | 25.55 km/h | 42.52 km/h |
| MODERATE | 250.56 sec | 187.03 sec | -25.5% | 25.73 km/h | 42.25 km/h |
| CONGESTED | 250.56 sec | 187.03 sec | -25.5% | 25.73 km/h | 42.25 km/h |
| SEVERE | 246.04 sec | 194.78 sec | -20.8% | 24.91 km/h | 40.90 km/h |

**Observation:** MODERATE and CONGESTED have **identical results** (down to the second).

---

### 3.2 Task 46: Audit Investigation

**Classification:** Defect detected; audit stopped before any new experiment

**Evidence Reviewed:**
1. Execution script: `docs/task45_results_experiments/._task45_runs.py`
2. Simulation service: `backend/api/simulation_service.py`
3. Task38 state replay mapping: `state_replay_mapping.csv`
4. Task45 results: `four_state_intervention_results.csv`
5. Raw execution JSON: `raw_four_state_results.json`

**Root Cause Analysis:**

**Finding 1: Identical Execution Context**

The Task45 execution script created identical SUMO configuration for both MODERATE and CONGESTED:
```
start_time='12:00'
end_time='13:00'
adoption_rate_pct=75.0
same origin and destination
same baseline route file path
same intervention route logic
```

**Finding 2: No State-Conditioned Demand Branch**

The `backend/api/simulation_service.py` implementation:
- Uses a **single baseline route file** for all states
- Uses single route assignment logic based on adoption rate only
- Does **not ingest a distinct state-conditioned demand from TomTom**
- Does not branch on FREE_FLOW vs MODERATE vs CONGESTED vs SEVERE

**Finding 3: Task38 Replay Mapping Explicitly States "STATE_SELECTION_ONLY"**

```csv
state,physical_reproduction,implementation_type
moderate,NO,STATE_SELECTION_ONLY
congested,NO,STATE_SELECTION_ONLY
```

**Finding 4: Prior Documentation (Task 43-44)**

Tasks 43 and 44 explicitly documented MODERATE and CONGESTED as selected but `NOT_RUN` states before Task45. The Task45 "results" cannot be interpreted as two independent physical traffic-state simulations.

**Formal Audit Conclusion:**

**DEFECT CONFIRMED:**

> "Four historical TomTom states were selected, but the available SUMO simulation context was not physically state-conditioned. Therefore, the four-state experiment demonstrates state selection/context replay rather than four distinct physical traffic-state simulations."

**Root Cause:** 
- The state labels (FREE_FLOW, MODERATE, CONGESTED, SEVERE) were applied to TomTom observations
- The SUMO simulation context was NOT changed per state
- MODERATE and CONGESTED therefore used identical SUMO runs, producing identical results
- This is a **labeling error in state context documentation**, not a state-conditioning implementation

**Evidence Artifacts:**  
[docs/task46_results_validity/TASK46_REPORT.md](docs/task46_results_validity/TASK46_REPORT.md) - Sections 1-4  
[docs/task46_results_validity/results_validity_audit.csv](docs/task46_results_validity/results_validity_audit.csv)  
[docs/task45_results_experiments/four_state_intervention_results.csv](docs/task45_results_experiments/four_state_intervention_results.csv)

---

## PART 4: SCIENTIFIC DEFENSIBILITY DETERMINATION

### Summary Table: Evidence Mapping

| Task | Classification | State Mapping Attempted? | Parameter Identified? | SUMO Runs Executed? | Artifact |
|---|---|---|---|---|---|
| 28 | C | YES (demand) | NO; non-monotonic response | NO | demand_sensitivity.csv |
| 36 | C | YES (all controls) | NO; no TomTom evidence for any | NO | sumo_control_surface.csv |
| 38 | C | YES; formal audit | **NOT_IDENTIFIABLE** for all 4 states | NO; intentionally withheld | state_parameterization.csv |
| 39 | B | N/A (observation service) | N/A | N/A (existing integration) | traffic_twin_service.py |
| 40 | B | N/A (UI integration) | N/A | Reused existing baseline | TrafficTwinPanel.jsx |
| 41 | B | N/A (demonstration only) | N/A | YES; one state demo (adoption-only) | sumo_baseline_vs_intervention.png |
| 42 | B | N/A (workflow documentation) | N/A | Reused existing baseline | TASK42_REPORT.md |
| 43 | B | N/A (multi-state demo) | N/A | 2/4 executed; 2/4 NOT_RUN | four_state_intervention_results.csv |
| 44 | B | N/A (hardening) | N/A | Same as Task 43 | TASK44_REPORT.md |
| 45 | B (with defect) | N/A (results only) | N/A | 4 claimed; 2 duplicates; 2 never run | **DEFECT: MODERATE=CONGESTED** |
| 46 | AUDIT STOP | N/A | N/A | NO (audit only) | results_validity_audit.csv |

---

### 4.1 Can FREE_FLOW, MODERATE, CONGESTED, SEVERE Be Mapped to SUMO Parameters?

**ANSWER: NO**

**Evidence:**

1. **Task 28 Demand Analysis:**
   - No valid demand multiplier formula exists
   - SUMO response was non-monotonic
   - Causal gate failed
   - Conclusion: "demand multiplier cannot be estimated defensibly"

2. **Task 36 Control Surface Audit:**
   - All 11 SUMO control categories evaluated (demand, speed, car-following, signals, routing, etc.)
   - TomTom provides NO identifying information for ANY category
   - Conclusion: "no TomTom field uniquely identifies any SUMO parameter"

3. **Task 38 Formal Parameterization:**
   - All four states explicitly marked `NOT_IDENTIFIABLE`
   - High confidence classification
   - No arbitrary parameter values invented
   - Conclusion: "no parameter is identified, no physical state-conditioned run is scientifically justified"

4. **Execution Evidence (Tasks 39-44):**
   - All four states map to same `BASELINE_SUMO` context
   - Implementation classified as `STATE_SELECTION_ONLY`
   - No demand branch, no parameter modification
   - Conclusion: Implementation correctly reflects evidence

**DEFENSIVE STATEMENT:** "We have no evidence. We are not inventing mappings. We are labeling observations and reusing a fixed SUMO context."

---

### 4.2 Correct Implementation Classification

**OPTIONS:**
- (A) Physically state-conditioned SUMO
- (B) Validated scenario-profile selection
- (C) State selection/context only

**DETERMINATION: (C) - State Selection/Context Only**

**Justification:**

1. **No validated mapping exists (eliminates A and B)**
   - Tasks 28-38 found no scientifically defensible mapping
   - No SUMO parameters can be identified from TomTom observations
   - No scenario-profile calibration was performed

2. **Current implementation is purely contextual**
   - TomTom state selects which historical observation is displayed
   - Underlying SUMO baseline, demand, network, route are FIXED
   - Intervention (adoption rate) is orthogonal to state
   - Comparison is always SUMO baseline vs. SUMO intervention (same state)

3. **Lineage explicitly enforces boundaries**
   - OBSERVED (TomTom values) ≠ SIMULATED (SUMO values)
   - UI clearly labels each layer
   - No circular reasoning or confusion
   - Service contracts are deterministic and testable

4. **Defect in Tasks 45-46 proves the point**
   - MODERATE and CONGESTED are identical because they use the same SUMO context
   - This shows that "state" is a TomTom label, not a SUMO control
   - If there were true state conditioning, MODERATE ≠ CONGESTED in simulation results

---

## PART 5: PROPOSED DATA LINEAGE DIAGRAM

```
TOMTOM REAL-WORLD OBSERVATIONS (EXTERNAL AUTHORITY)
│
├─ Raw JSON: 161 historical captures (June 11-19, 2026)
│  ├─ current_speed (km/h)
│  ├─ free_flow_speed (km/h)  
│  ├─ current_travel_time (s)
│  ├─ free_flow_travel_time (s)
│  ├─ timestamp (ISO 8601)
│  └─ incident_count
│
└─→ DERIVATION: Normalized Metrics (Task 35-39)
   ├─ speed_ratio = current_speed / free_flow_speed
   ├─ speed_loss = 1 - speed_ratio
   ├─ travel_time_ratio = current_travel_time / free_flow_travel_time
   ├─ delay_ratio = travel_time_ratio - 1
   └─ congestion_state ∈ {FREE_FLOW, MODERATE, CONGESTED, SEVERE}
      └─ (via quartile binning; Task 35 trained)
│
└─→ STATE CONTEXT SELECTION (TrafficTwinService.py)
   ├─ Query: exact timestamp lookup
   ├─ Returns: 161-row historical state table
   ├─ Uniqueness: timestamp-based deterministic lookup
   └─ Preservation: OBSERVED/DERIVED/SIMULATED lineage
│
└─→ UIDT WORKFLOW INPUT (Command Center)
   │
   ├─→ OBSERVATION PANEL (Read-only)
   │   ├─ Display: TomTom speeds, travel times, state label
   │   ├─ Context: "What was the real world doing at this time?"
   │   └─ Assurance: No data modification, no injection into SUMO
   │
   └─→ SIMULATION CONTEXT ATTACHMENT
       │
       ├─ Selected baseline SUMO configuration (FIXED)
       │  ├─ Network: auto_tool/cleaned_network.net.xml
       │  ├─ Demand: auto_tool/simulation.rou.xml (5,450 vehicles)
       │  ├─ Route set: baseline + alternative routes (1-4)
       │  ├─ Signals: native SUMO coordination
       │  └─ Simulation window: 19:00-20:00 (1 hour)
       │
       └─→ INTERVENTION MECHANISM (Existing; Not State-Driven)
           │
           ├─ Adoption-only intervention
           │  ├─ Baseline route assignment: deterministic + stochastic
           │  ├─ Intervention: redirect 0-100% to Route 4
           │  └─ No demand change, no behavioral parameter change
           │
           └─→ SUMO EXECUTION (Native Simulator)
               │
               ├─ Input: Fixed baseline + fixed intervention
               ├─ Output: Native SUMO edgeData, tripinfo, summary
               ├─ Metrics: travel_time, speed, delay, throughput
               └─ Comparison: Baseline vs. Intervention ONLY
                  (TomTom state has NO bearing on SUMO parameters)
│
└─→ RESULTS LAYER (Simulated Decision Support)
    │
    ├─ Baseline metrics: speed, travel time, throughput
    ├─ Intervention metrics: speed, travel time, throughput
    ├─ Adoption rate: 0-100% of demand diverted to Route 4
    ├─ Improvement: (Intervention - Baseline) / Baseline
    └─ CRITICAL: Results are SUMO-vs-SUMO comparison only
       The TomTom state is the HISTORICAL CONTEXT that selected
       which time period to analyze, NOT a validation target or
       demand input.
```

---

## PART 6: MINIMUM EXPERIMENTS REQUIRED BEFORE PHYSICAL STATE-CONDITIONING

**If the goal is to move from (C) to (A) physically state-conditioned SUMO:**

### 6.1 Required Independent Data Sources

At minimum, **one** of the following must be acquired and aligned with TomTom observations:

| Data Type | Purpose | Collection Effort | Alignment Risk |
|---|---|---|---|
| **Vehicle count detector (loops/radar)** | Validate demand multiplier | Medium | Low if static location |
| **GPS trajectory dataset** | Extract OD and behavioral params | High | Medium; privacy/coverage |
| **Lane-level speed/occupancy (toll plaza or HOT lanes)** | Validate per-segment state | Medium | Medium; requires matched corridor |
| **Signal timing + detector phases** | Map state to signal conditions | High | High; requires controller access |
| **Per-edge flow measurement (Bluetooth/WiFi)** | Validate route-level assignment | Low-Medium | Medium; privacy concerns |
| **Complete incident log (registered edge + duration + type)** | Map incidents to network disruption | Medium | High; manual registration needed |

---

### 6.2 Experiment 1: Demand Monotonicity Validation

**Objective:** Prove that a causal demand-to-state relationship is reproducible and monotonic.

**Design:**
- Run SUMO with 0.5x, 0.75x, 1.0x, 1.25x, 1.5x demand multipliers
- Use **identical network, route file, and time window** for all runs
- Measure corridor speed, travel time, occupancy for each scenario
- Compare to actual TomTom observations for overlapping time period

**Success Criterion:**
- Monotonic response: higher demand → lower speed, higher delay
- Within ±5% of observed TomTom metrics (after alignment)
- Reproducible across 5 repeated runs (deterministic seeding)

**Failure Handling:**
- If response remains non-monotonic: **DO NOT PROCEED**
- Root cause: inherited invalid routes, incomplete vehicle populations, or corridor measurement error
- Fix required: stabilize SUMO model first (Tasks 29-34 recommendations)

**Evidence Artifacts Required:**
- `demand_response_validation.csv`: demand multiplier, requested vehicles, inserted vehicles, corridor speed, corridor delay
- `demand_tomtom_alignment.csv`: SUMO times aligned to TomTom observations by timestamp
- `monotonicity_plot.png`: visual verification of monotonic response

---

### 6.3 Experiment 2: State-to-Parameter Mapping via Detector Data

**Objective:** Identify which SUMO parameter(s) correspond to observed TomTom state changes.

**Design:**
- Deploy or acquire static loop/radar detector data for corridor
- Count vehicles by time interval (15-min or hourly)
- Correlate detector counts to TomTom speed_ratio and congestion_state
- Run SUMO with detected demand (from Experiment 1 validation)
- Compare SUMO metrics to real-world detector observations

**State-to-Demand Mapping Example (if successful):**
```
TOMTOM State        | Speed Ratio | Detector Vehicle Count | SUMO Demand Multiplier | SUMO Speed
FREE_FLOW           | 1.0         | 400-500 veh/hr        | 0.5x                   | 36 km/h ✓
MODERATE            | 0.80-0.95   | 800-1200 veh/hr       | 0.9x                   | 28-32 km/h ✓
CONGESTED           | 0.70-0.80   | 1200-1600 veh/hr      | 1.2x                   | 20-28 km/h ✓
SEVERE              | <0.70       | >1600 veh/hr          | 1.5x                   | <20 km/h ✓
```

**Validation Criterion:**
- Within ±10% of observed TomTom metrics after alignment
- Mapping is **unique and reversible**: given TomTom speed_ratio, can predict demand multiplier and vice versa
- Reproducible across multiple historical days (training/validation split)

**Evidence Artifacts Required:**
- `detector_tomtom_correlation.csv`: detector count, TomTom state, speed ratio
- `state_demand_mapping_table.csv`: the final mapping (as above)
- `state_demand_mapping_confidence.csv`: correlation coefficient, p-value, error bounds

---

### 6.4 Experiment 3: Behavioral Parameter Identification (Secondary)

**Objective (only if Experiment 2 succeeds):** Determine whether speed ratio is a proxy for demand multiplier alone, or whether it also reflects behavioral changes (desired speed, car-following, lane-changing).

**Design:**
- Run SUMO with identical demand multiplier but varying car-following tau, desired speed, and lane-changing aggressiveness
- Measure speed, delay, and occupancy for each behavioral configuration
- Compare to TomTom observations to see if behavioral tuning is needed

**Success Criterion:**
- Demand multiplier alone explains ≥95% of speed ratio variance (behavioral changes contribute <5%)
- OR behavioral parameters are identifiable from detector/trajectory data

**Failure Handling:**
- If behavioral changes account for >10% of variance: a more complex model is needed
- Do not invent behavioral parameters from TomTom alone; require independent data

**Evidence Artifacts Required:**
- `behavioral_sensitivity_analysis.csv`: tau, desired_speed, lcSigma, speed_ratio_error
- `behavioral_contribution_variance_decomposition.csv`: demand contribution %, behavioral contribution %

---

### 6.5 Experiment 4: Held-Out Validation on Unseen Time Periods

**Objective:** Prove that the state-to-SUMO mapping generalizes to new data.

**Design (only perform if Experiments 1-3 succeed):**
- Divide TomTom observations into training (June 11-15) and validation (June 16-19)
- Fit state-to-demand mapping on training period
- Run SUMO with predicted demand on validation periods
- Measure prediction error on held-out validation states

**Success Criterion:**
- Mean Absolute Percentage Error (MAPE) ≤ 15% for travel time
- MAPE ≤ 10% for speed ratio
- Error is uncorrelated with time-of-day or congestion state (no systematic bias)

**Failure Handling:**
- If MAPE > 15%: mapping does not generalize; external factors are influencing state (incidents, special events, route closures)
- Conclusion: state-to-demand is not a stable relationship; higher-fidelity data needed

**Evidence Artifacts Required:**
- `held_out_validation_results.csv`: timestamp, tomtom_state, predicted_demand, actual_demand, sumo_speed, tomtom_speed, error_pct
- `validation_error_analysis.png`: residual plot, Q-Q plot, time series of error

---

### 6.6 Experiment 5: Intervention Generalization (If Moving to Production)

**Objective:** Prove that intervention benefits hold across the four state regimes, not just one.

**Design (only if Experiments 1-4 succeed):**
- Use the validated state-to-demand mapping from Experiment 2
- Run full baseline/intervention pairs for **all four states**: FREE_FLOW, MODERATE, CONGESTED, SEVERE
- Each pair must use the **state-specific demand profile** identified in Experiment 2
- Measure intervention benefit (% travel time reduction, % speed improvement)

**Success Criterion:**
- Intervention benefit is **positive and consistent** across all four states (not state-dependent anomalies)
- OR intervention benefit is **monotonic** in demand level (if it varies, it should be predictable)
- No state produces zero or negative intervention benefit due to capacity saturation

**Failure Handling:**
- If intervention benefit is negative in any state (intervention makes congestion worse): 
  - Root cause: Intervention route capacity limit exceeded at high demand
  - Solution: Scale intervention capacity or adjust adoption rate by state
  - OR: Intervention not suitable for SEVERE state; use different mechanism
- If benefit is highly state-dependent: state-specific tuning required; not a universal intervention

**Evidence Artifacts Required:**
- `multi_state_intervention_results.csv`: state, baseline_time, intervention_time, improvement_pct, adoption_rate, confidence
- `state_conditioned_intervention_calibration.csv`: state, demand_multiplier, optimal_adoption_rate, max_benefit
- `production_deployment_decision.md`: Go/No-Go recommendation based on generalization results

---

## PART 7: RISK AND RECOMMENDATION

### 7.1 Current Risk Assessment

| Risk | Severity | Current Mitigation | Evidence |
|---|---|---|---|
| **State confusion**: Results claimed as state-conditioned when they are context-only | HIGH | Explicit lineage enforcement; defect found in Task 45-46 | state_replay_mapping.csv shows STATE_SELECTION_ONLY; MODERATE=CONGESTED defect |
| **Unmapped demand variability**: TomTom state changes not explained by identified SUMO parameter | CRITICAL | No state-to-parameter mapping attempted; data not collected | Task 38 state_parameterization.csv: all NOT_IDENTIFIABLE |
| **Non-monotonic response**: Demand changes do not reliably produce predictable state changes | CRITICAL | Validation required before production | Task 28 demand_sensitivity.csv: high demand (8,175) faster than baseline (5,450) |
| **Generalization failure**: Intervention benefits observed in one scenario may not hold in others | HIGH | Multi-state demo partially executed; defect found | Task 45-46: MODERATE and CONGESTED are duplicates |
| **Undocumented assumptions**: Implicit claims that TomTom validates SUMO | MEDIUM | Explicit "NOT PERFORMED" markers; boundary documentation | task38_REPORT: "not scientifically justified"; task39_REPORT: "never claimed to reproduce" |

---

### 7.2 Recommendations

#### **IMMEDIATE (Production Safety):**

1. **Fix Task 45-46 Defect**
   - Mark MODERATE results as `NOT_RUN` (truth: they were never executed independently)
   - Revise four-state results table to show only FREE_FLOW and SEVERE (2 executed, 2 not run)
   - Update TASK45_SUMMARY.json to reflect corrected status
   - Artifact: `four_state_intervention_results_CORRECTED.csv`

2. **Enforce Data Lineage in Frontend UI**
   - Add visual indicator: "Results shown: SUMO baseline vs. SUMO intervention (state provides context only)"
   - Separate panel: "TomTom observation" | "SUMO simulation results"
   - Do not display TomTom speed and SUMO speed on the same axis or as if they should match
   - Link: Add explainer: "Why SUMO and TomTom values differ" (different samples, different modeling)

3. **Update API Contract Documentation**
   - Explicitly state in `traffic_twin_api_contract.json`: "State selection does not modify SUMO parameters"
   - Add deprecation warning: "Version 1.0 uses fixed baseline for all states. Version 2.0 (if built) would require validated state-to-parameter mapping before release."
   - Signature: Require explicit acknowledgment in UI: "I understand intervention results are simulations, not real-world calibration."

---

#### **SHORT TERM (Next 3 Months; Optional):**

1. **Run Experiment 1: Demand Monotonicity Validation**
   - Effort: 1-2 weeks SUMO execution + analysis
   - If successful: Proceed to Experiment 2
   - If failed: Diagnose inherited route file and demand insertion issues (Task 29 recommendations)
   - Decision gate: Do not proceed past this without passing monotonicity test

2. **Acquire One Independent Data Source**
   - Simplest: Static detector loop count data (if available from traffic authority)
   - Moderate: Per-corridor Bluetooth travel-time data (may already exist)
   - Plan: 2-4 weeks for data acquisition + alignment
   - Success: Enables Experiment 2 (state-to-demand mapping validation)

3. **Document Why Tests Were Not Run in Tasks 28-38**
   - Write: "Validation_Gap_Report.md"
   - Explain: Non-monotonic response in Task 28, no identifiable parameters in Task 36-38
   - Provide: Decision rationale for stopping at state-selection-only (correct decision)
   - Audience: Internal review + external publication transparency

---

#### **MEDIUM TERM (3-12 Months; Only If Funded):**

**DO NOT ATTEMPT without first passing Experiment 1-2**

1. **Run Full Experiment Suite (1-4)**
   - Demand monotonicity validation (Exp 1)
   - State-to-parameter mapping (Exp 2)
   - Behavioral parameter sensitivity (Exp 3; optional)
   - Held-out validation (Exp 4)
   - Effort: 6-10 weeks total (includes SUMO runs, data alignment, analysis)
   - Funding: ~1 FTE researcher + compute

2. **Establish Production Readiness Gate**
   - Pass criteria: MAPE ≤ 15% on held-out validation (Experiment 4)
   - Ownership: Principal investigator + external validator
   - Approval: Cannot ship state-conditioned SUMO without passing gate

3. **Upgrade to (A) Physically State-Conditioned SUMO**
   - Only if Experiments 1-4 pass and held-out validation succeeds
   - Implementation: Modify `simulation_service.py` to accept state and inject demand multiplier
   - Testing: Re-validate on new time period before release

---

### 7.3 Current Recommendation: **MAINTAIN IMPLEMENTATION AS (C)**

**DO NOT ATTEMPT OPTION (A) UNTIL EXPERIMENTS 1-4 PASS.**

**Rationale:**

1. **Evidence Base:**
   - Tasks 28-38 found zero identifiable mappings
   - Task 38 explicitly declared state parameters `NOT_IDENTIFIABLE`
   - Task 46 audit found defect in claimed four-state results (MODERATE=CONGESTED)
   - No new data has been acquired

2. **Scientific Integrity:**
   - Current implementation is honest: state selection + fixed SUMO context
   - Trying to force state conditioning without data would require inventing parameters
   - Tasks 39-44 correctly maintained boundary; only Task 45 had labeling error (now documented)

3. **Risk/Benefit:**
   - **Benefit of attempting (A) without data:** Minimal; would be artificial
   - **Cost of attempting (A) without data:** High; publication error, loss of credibility
   - **Benefit of staying at (C) + clear documentation:** Maximum; honest result, reproducible service
   - **Cost of staying at (C):** Slower scientific progress, but correct methodology

4. **Next Phase:**
   - Implement Experiments 1-2 (demand validation + detector correlation)
   - If successful: Upgrade to (B) validated scenario-profile selection
   - If (B) works: Then consider (A) physically state-conditioned SUMO
   - Timeline: 3-6 months before attempting upgrade

---

## PART 8: SUPPORTING DATA ARTIFACTS SUMMARY

### Validation Gap Evidence

| Task | Result | Artifact | Status |
|---|---|---|---|
| 28 | Non-monotonic demand response | demand_sensitivity.csv | GATE FAILED |
| 36 | No identifiable SUMO control | sumo_control_surface.csv | NO MAPPING |
| 38 | All states NOT_IDENTIFIABLE | state_parameterization.csv | **CRITICAL** |
| 38 | All states STATE_SELECTION_ONLY | state_replay_mapping.csv | **CRITICAL** |
| 45-46 | MODERATE=CONGESTED defect | four_state_intervention_results.csv + results_validity_audit.csv | **DEFECT** |

### Implementation Boundary Artifacts

| Component | File | Classification | Evidence |
|---|---|---|---|
| TrafficTwinService API | traffic_twin_service.py | Read-only observation | No demand injection |
| TrafficTwinPanel UI | TrafficTwinPanel.jsx | Labeled lineage | OBSERVED/DERIVED/SIMULATED |
| Data lineage | traffic_twin_data_lineage.csv | Fixed baseline for all states | demand_context="BASELINE_SUMO" (all rows) |
| Replay mapping | state_replay_mapping.csv | No physical reproduction | ALL states: physical_reproduction="NO" |

---

## CONCLUSION

**Scientific Finding:** No defensible evidence exists to map FREE_FLOW, MODERATE, CONGESTED, SEVERE to SUMO physical parameters using current data.

**Current Implementation Status:** Correctly implemented as **(C) State Selection/Context Only**. TomTom state labels observations; SUMO baseline remains fixed across all states.

**Defect Found:** Task 45-46 claimed four-state results but MODERATE and CONGESTED are duplicates (same SUMO context). Now documented and stopped.

**Path Forward:** Run Experiments 1-2 (demand monotonicity + detector mapping) before attempting upgrade. Estimated 3-6 months if funded.

**Recommendation:** Maintain current implementation; document validation gaps; do not force state conditioning without evidence.

---

**Audit Completed:** 2026-08-18  
**Status:** STOP CONDITION REACHED; NO PRODUCTION CODE CHANGES MADE  
**Next Review:** When Experiments 1-2 complete (proposed for Q4 2026)
