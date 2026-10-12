# UIDT State-Conditioning: Minimum Experiments for Implementation Decision

**Date:** 2026-08-18  
**Purpose:** Define experiments required to upgrade from (C) State Selection/Context to (A) Physically State-Conditioned SUMO  
**Decision Gate:** Cannot ship state-conditioned SUMO without passing all experiments 1-4  

---

## Executive Summary

| Experiment | Duration | Cost | Blocking Issue | Pass/Fail Criterion | Artifact |
|---|---|---|---|---|---|
| **1: Demand Monotonicity** | 1-2 weeks | 1 FTE research + compute | Non-monotonic response Task 28 | Monotonic ±5% or STOP | demand_response_validation.csv |
| **2: State-to-Demand Mapping** | 3-4 weeks | 1 FTE + detector data acquisition | No independent data | State→demand unique; MAPE≤10% | state_demand_mapping_table.csv |
| **3: Behavioral Sensitivity** | 2 weeks | 1 FTE (optional) | Only if demand <95% explains | Demand ≥95% variance | behavioral_sensitivity.csv |
| **4: Held-Out Validation** | 2-3 weeks | 1 FTE + compute | Generalization risk | MAPE≤15% travel time, ≤10% speed | held_out_validation_results.csv |
| **5: Multi-State Intervention** | 2-3 weeks | 1 FTE + compute | Generalization to all 4 states | Consistent benefit ≥0% all states | multi_state_intervention_results.csv |

**Timeline:** ~10-14 weeks if continuous; ~3-6 months including data acquisition + team coordination

**Go/No-Go Decision:** Upstream manager review after Experiment 2 (week 4)

---

## Experiment 1: Demand Monotonicity Validation

### Objective

Reproduce and stabilize the SUMO demand-response relationship that failed in Task 28. This is a **blocking prerequisite** for any downstream mapping.

### Scientific Question

*Does a monotonic, causal relationship exist between SUMO demand multiplier and corridor traffic state metrics (speed, delay, throughput)?*

### Hypothesis

> Null (H0): SUMO response to demand changes is non-monotonic or random
> 
> Alternative (H1): SUMO response is monotonically decreasing in speed and monotonically increasing in delay as demand multiplier increases from 0.5x to 1.5x

### Experimental Design

#### Setup

| Parameter | Value | Justification |
|---|---|---|
| Network | `auto_tool/cleaned_network.net.xml` | Existing production |
| Base demand file | `auto_tool/simulation.rou.xml` | 5,450 vehicles; production baseline |
| Simulation period | 25200-86400 sec (7:00-24:00) | Covers peak hours; exclude warm-up |
| Time step | 1 second | Native SUMO precision |
| Seed | 42 | Deterministic for reproducibility |
| Runs per scenario | 5 | Account for stochastic variability |
| Total runs | 5 scenarios × 5 replicates = 25 runs | |
| Demand scenarios | 0.5x, 0.75x, 1.0x, 1.25x, 1.5x | Cover full range; test insertion limits |

#### Demand Generation

For each scenario, create a new demand file by:

```python
scenario_multiplier = {
  "0.5x":   0.50,
  "0.75x":  0.75,
  "1.0x":   1.00,
  "1.25x":  1.25,
  "1.5x":   1.50,
}

for scenario in scenario_multiplier:
    vehicles_to_insert = int(5450 * scenario_multiplier[scenario])
    sort_routes_by_departure_time()  # stable insertion order
    write_scenario_rou_xml(vehicles_to_insert)
```

Vehicles are deterministically sorted by departure time before writing to ensure reproducibility.

#### Measurements

For each run, extract native SUMO edgeData (900-second intervals) for the six overlap corridor edges (Task 28):

```text
244031183#2
1248797211
479981468#1
1368403128#1
1148480236#3
1167340905
```

Calculate corridor-level metrics:

```
corridor_speed = total_edge_length / sum(edge_travel_times_over_interval)
corridor_delay = sum(time_loss_over_edges) / total_vehicles_on_edges
corridor_occupancy = mean(edge_occupancy_over_interval)
corridor_throughput = sum(vehicles_leaving_edges) / interval_duration
inserted_vehicles = count(departed_vehicles)
completed_vehicles = count(arrived_vehicles)
unfinished_vehicles = count(still_in_network_at_end)
```

#### Output Format

**File:** `demand_response_validation.csv`

```csv
run_id,replicate,scenario,demand_multiplier,requested_vehicles,inserted_vehicles,completed_vehicles,unfinished_vehicles,corridor_speed_kmh,corridor_delay_s,corridor_occupancy_pct,corridor_throughput_veh_per_s,mean_speed_ratio,seed
1,1,0.5x,0.5,2725,2725,2705,20,38.2,755.6,12.1,0.91,0.922,42
2,2,0.5x,0.5,2725,2725,2701,24,37.9,742.3,11.8,0.88,0.915,43
...
25,5,1.5x,1.5,8175,8117,7891,226,35.0,85000.0,48.3,1.87,0.844,46
```

#### Statistical Analysis

For each metric, compute:
- Mean and standard deviation across 5 replicates
- Coefficient of variation (std / mean)
- 95% confidence interval
- Linear trend slope (demand multiplier vs. metric)
- R² of linear fit

```python
# Example
for metric in [corridor_speed, corridor_delay, corridor_occupancy]:
    by_scenario = group_by_demand_multiplier(results)
    for scenario in by_scenario:
        mean = np.mean(by_scenario[scenario][metric])
        std = np.std(by_scenario[scenario][metric])
        ci95 = 1.96 * std / np.sqrt(5)
        print(f"{scenario}: {mean:.2f} ± {ci95:.2f} ({std/mean*100:.1f}% CV)")
    
    # Linear regression: demand_multiplier → metric
    X = np.array([0.5, 0.75, 1.0, 1.25, 1.5])
    Y = np.array([means by scenario])
    slope, intercept, r_value, p_value, std_err = linregress(X, Y)
    print(f"Slope: {slope:.2f}, R²: {r_value**2:.3f}, p-value: {p_value:.4f}")
```

### Success Criterion (Pass/Fail Gate)

**PASS if ALL of the following:**

1. **Monotonicity (Required):**
   - Corridor speed: Monotonically decreases as demand increases from 0.5x to 1.5x
   - Corridor delay: Monotonically increases as demand increases from 0.5x to 1.5x
   - Tolerance: No scenario should be an outlier (>±10% from linear trend)

2. **Reproducibility:**
   - Coefficient of variation within each scenario ≤ 10% (i.e., std / mean ≤ 0.10)
   - 95% confidence intervals do not overlap between adjacent scenarios (0.5x vs 0.75x, 0.75x vs 1.0x, etc.)

3. **Insertion Success:**
   - Requested vehicles ≈ inserted vehicles for 0.5x, 0.75x, 1.0x (allow <1% difference)
   - For 1.25x and 1.5x, document any insertion failures explicitly

4. **Response Magnitude:**
   - Difference in corridor speed between 0.5x and 1.5x ≥ 5 km/h (meaningful signal)
   - Difference in corridor delay between 0.5x and 1.5x ≥ 20% (meaningful signal)

**FAIL if:**
- Non-monotonic response observed (e.g., 1.5x faster than 1.0x, like Task 28)
- Coefficient of variation >10% in any scenario (too noisy for reliable mapping)
- Insertion failures >10% in baseline scenario (route file problem)
- Any unexpected SUMO warnings or errors in >1 of 25 runs

### Failure Handling

If this experiment **FAILS:**

**Action:** STOP all downstream work. Investigate and fix one of:

1. **Inherited invalid routes:** Some vehicles in `simulation.rou.xml` cannot depart on their assigned edges (Task 28 known issue)
   - Fix: Audit route file; remove stub routes; update Task 28 baseline
   
2. **Incomplete vehicle populations:** Vehicles not completing departure/arrival in simulation window
   - Fix: Extend simulation period OR investigate route/edge capacity limits
   
3. **Corridor measurement error:** The six overlap edges do not form a valid directed path
   - Fix: Re-validate edge IDs; add/remove nearby edges; verify connectivity
   
4. **SUMO version or configuration issue:** Unexpected behavior from simulator
   - Fix: Reproduce Task 28 baseline; compare outputs; update SUMO if needed

After root-cause fix, re-run Experiment 1. Do not proceed to Experiment 2 until this passes.

### Decision Gate

**Upstream manager review:** Upon completion, present:
- Monotonicity plot (demand multiplier vs. corridor speed/delay)
- Confidence intervals for each scenario
- Summary: "SUMO demand response is [MONOTONIC/NON-MONOTONIC] and [REPRODUCIBLE/NOISY]"
- Recommendation: [PROCEED to Experiment 2] OR [STOP; fix SUMO model]

---

## Experiment 2: State-to-Demand Mapping via Detector Data

### Objective

Identify the specific SUMO demand multiplier that corresponds to each TomTom traffic state (FREE_FLOW, MODERATE, CONGESTED, SEVERE).

### Scientific Question

*Can we establish a unique, reversible mapping from TomTom congestion_state → SUMO demand multiplier using independent vehicle count data?*

### Hypothesis

> Null (H0): TomTom state and vehicle count are unrelated; no consistent mapping exists
> 
> Alternative (H1): Each TomTom state corresponds to a distinct vehicle-count range; the mapping is unique (one-to-one)

### Experimental Design

#### Step 1: Acquire Independent Vehicle Count Data

**Timeline:** 2-3 weeks (data procurement)

**Options (in order of preference):**

| Source | Coverage | Access | Cost | Timeline | Example |
|---|---|---|---|---|---|
| **1. Static loop detector** | Single location on corridor | Traffic authority | Low | 1-2 weeks | Bangalore Municipal Corporation has installed loops on major roads |
| **2. Bluetooth probe data** | Corridor segment (if corridor has toll/detection) | Toll operator or app | Low-Medium | 1-2 weeks | Travel time data; can infer count |
| **3. GPS trajectory data** | Full corridor if exists | Ride-hailing company or research institute | Medium-High | 2-4 weeks | Bangalore Traffic Monitor (BTM) or academic datasets |
| **4. Manual count** | Site-based; full corridor | Field team | High (labor) | 2-3 weeks | 1-hour video count at entry/exit points |

**Chosen approach:** Use **existing detector data** if available from Task 21-22 corridor audit; otherwise acquire static loop data.

#### Step 2: Align TomTom States to Vehicle Counts

**Data:** 161 TomTom observations (June 2026) with timestamps

**Process:**

```python
# For each TomTom observation
for tomtom_obs in observations:
    timestamp = tomtom_obs.timestamp  # e.g., 2026-06-13 19:00:03
    state = tomtom_obs.congestion_state  # FREE_FLOW, MODERATE, CONGESTED, SEVERE
    speed_ratio = tomtom_obs.speed_ratio  # 0.694
    
    # Find corresponding vehicle count from detector
    # (allow ±15 min window for detector granularity)
    detector_obs = detector_data.get_by_timestamp(timestamp, window=15_min)
    
    if detector_obs:
        vehicle_count = detector_obs.vehicle_count  # veh/hour
        
        # Record triplet
        output.append({
            'timestamp': timestamp,
            'tomtom_state': state,
            'tomtom_speed_ratio': speed_ratio,
            'detector_vehicle_count': vehicle_count,
            'alignment_confidence': 'EXACT' or 'WITHIN_15MIN'
        })
```

**Output:** `tomtom_detector_alignment.csv` (all 161 aligned pairs)

**Acceptance Criterion:** ≥90% of TomTom observations can be aligned to a detector observation within ±15 minutes

#### Step 3: Derive State-to-Demand Mapping

**Analysis:**

For each state, compute:

```python
for state in [FREE_FLOW, MODERATE, CONGESTED, SEVERE]:
    observations_in_state = filter(alignment_df, tomtom_state == state)
    vehicle_counts = observations_in_state.detector_vehicle_count
    
    # Statistics
    count_min = vehicle_counts.min()
    count_max = vehicle_counts.max()
    count_mean = vehicle_counts.mean()
    count_median = vehicle_counts.median()
    count_std = vehicle_counts.std()
    
    # Pearson correlation: vehicle_count ↔ speed_ratio
    correlation, p_value = pearsonr(vehicle_counts, 
                                     observations_in_state.tomtom_speed_ratio)
    
    print(f"{state}: {count_min}-{count_max} veh/hr; "
          f"mean={count_mean:.0f}±{count_std:.0f}; "
          f"correlation with speed_ratio={correlation:.3f} (p={p_value:.4f})")
```

**Example Output:**

```
FREE_FLOW:   400-650 veh/hr; mean=500±80; correlation=0.92 (p<0.001)
MODERATE:    800-1300 veh/hr; mean=1050±150; correlation=0.88 (p<0.001)
CONGESTED:   1200-1700 veh/hr; mean=1450±120; correlation=0.91 (p<0.001)
SEVERE:      1600-2100 veh/hr; mean=1850±140; correlation=0.89 (p<0.001)
```

#### Step 4: Convert Vehicle Counts to SUMO Demand Multipliers

**Baseline:** 5,450 vehicles in production route file (Task 28)

**Conversion:**

```python
# From Task 28 experiment:
# 5,450 baseline vehicles → observed in detector data during baseline period

# Find: what detector count corresponds to baseline 5,450 vehicles?
baseline_detector_count_per_hour = ???  # To be measured

# Then: for each state, compute multiplier
for state in [FREE_FLOW, MODERATE, CONGESTED, SEVERE]:
    state_detector_count = mean_detector_count_for_state[state]
    demand_multiplier = state_detector_count / baseline_detector_count_per_hour
    
    output[state] = {
        'detector_vehicle_count': state_detector_count,
        'sumo_demand_multiplier': demand_multiplier,
        'example_sumo_vehicles': int(5450 * demand_multiplier)
    }
```

**Output:** `state_demand_mapping_table.csv`

```csv
tomtom_state,detector_vehicle_count_veh_per_hr,detector_count_ci95,pearson_corr_with_speed_ratio,sumo_baseline_demand_per_hr,sumo_demand_multiplier,sumo_example_vehicles,confidence
FREE_FLOW,500,450-550,0.92,5450,0.092,500,HIGH
MODERATE,1050,900-1200,0.88,5450,0.193,1050,HIGH
CONGESTED,1450,1300-1600,0.91,5450,0.266,1450,MEDIUM
SEVERE,1850,1700-2000,0.89,5450,0.339,1850,MEDIUM
```

**Uniqueness Check:**

- Are ranges non-overlapping? (FREE_FLOW vs MODERATE vs CONGESTED vs SEVERE)
- If ranges overlap: mapping is NOT unique; FAIL this experiment

### Success Criterion (Pass/Fail Gate)

**PASS if ALL of the following:**

1. **Alignment Coverage:** ≥90% of TomTom observations aligned to detector data (within ±15 min window)

2. **Uniqueness:** Vehicle-count ranges for the four states do NOT overlap
   - Example: FREE_FLOW (400-650) and MODERATE (800-1300) ranges don't overlap ✓
   - Counter-example: SEVERE (1600-2100) and CONGESTED (1200-1700) overlap ✗

3. **Correlation Strength:** Pearson correlation between detector vehicle count and TomTom speed_ratio ≥ 0.75 for all four states
   - (High correlation = strong causal relationship)

4. **Reversibility:** Given a vehicle count, can uniquely determine state; vice versa

5. **Reproducibility:** Re-align same observations with fresh detector data → identical mapping (±5% tolerance)

6. **Training MAPE:** Map predictions on training period (June 11-15):
   - Train mapping on this period
   - Predict SUMO speed for observed states
   - Compare to actual TomTom speed_ratio
   - **MAPE ≤ 10% on training period** (Experiment 4 will test generalization)

**FAIL if:**

- Alignment coverage <90% (insufficient data)
- Overlapping vehicle-count ranges for different states (not unique)
- Pearson correlation <0.75 for any state (weak relationship)
- Training MAPE >15% (mapping doesn't fit data well)

### Failure Handling

If this experiment **FAILS:**

**Action:** STOP. Investigate root cause:

1. **Insufficient detector data:** Vehicle counts don't span full dynamic range of TomTom states
   - Fix: Extend measurement period; acquire different detector (e.g., toll plaza with higher volume)

2. **Detector data misaligned:** Timestamp zones or measurement intervals don't match TomTom
   - Fix: Manually verify 5-10 alignments; adjust alignment window or convert detector timestamps

3. **External confounding factors:** Vehicle count ≠ TomTom speed_ratio due to incidents, events, temporary closures
   - Fix: Filter alignment to exclude flagged incident periods; re-run analysis
   
4. **TomTom non-monotonic with demand:** Like Task 28, higher vehicle counts produce higher speeds (paradox)
   - Fix: This suggests deeper issue with corridor model; go back to Experiment 1 failure handling

After root-cause investigation, report findings to upstream manager. Decide whether to:
- Re-acquire better detector data and retry
- Adjust TomTom state definitions (change quartile thresholds)
- Stop and conclude that state-to-demand mapping is not identifiable

### Decision Gate

**Upstream manager review:** Upon completion, present:
- State-demand mapping table (four states, unique ranges)
- Scatter plot: vehicle count vs. TomTom speed_ratio (should show clear clusters)
- Pearson correlations and confidence intervals
- Summary: "State-to-demand mapping is [UNIQUE/AMBIGUOUS]; ready for Experiment 3-4" OR "[NOT_IDENTIFIABLE]"
- Recommendation: [PROCEED to Experiment 3/4] OR [STOP; re-evaluate approach]

---

## Experiment 3: Behavioral Parameter Sensitivity (Optional)

**Skip this experiment if Experiment 2 shows that demand multiplier alone explains ≥95% of state variance.**

### Objective

Determine whether behavioral changes (desired speed, car-following, lane-changing) contribute significantly to state changes, or if demand multiplier is the primary driver.

### Scientific Question

*Is TomTom congestion_state primarily a function of demand, or do behavioral parameters (e.g., driver aggressiveness, lane-changing) also play a significant role?*

### Experimental Design

#### Setup

| Parameter | Value |
|---|---|
| Demand scenarios | From Experiment 2: FREE_FLOW (0.092x), MODERATE (0.193x), CONGESTED (0.266x), SEVERE (0.339x) |
| Behavioral parameters to vary | tau (car-following response time), lcSigma (lane-change variance), desired speed |
| Baseline tau | 1.0 sec (SUMO default) |
| Test tau values | 0.5, 1.0, 1.5, 2.0 sec |
| Baseline lcSigma | 0.02 (SUMO default) |
| Test lcSigma values | 0.01, 0.02, 0.04 sec |
| Baseline desired speed | 1.0 speedFactor (100% of max speed) |
| Test desired speed | 0.8, 1.0, 1.2 speedFactor |
| Total runs | 4 states × 4 tau × 3 lcSigma × 3 speedFactor = 144 runs |

#### Measurement

For each run, measure:
- Corridor speed
- Corridor delay
- Lane change frequency
- Vehicle headways

Compare to **Experiment 2 baseline results** (demand multiplier alone, default behavioral parameters).

#### Analysis

**Variance decomposition:**

```python
# Fit linear model: corridor_speed ~ demand_multiplier + tau + lcSigma + speedFactor
import statsmodels.api as sm

data = pd.read_csv('behavioral_sensitivity_results.csv')
X = data[['demand_multiplier', 'tau', 'lcSigma', 'speedFactor']]
y = data['corridor_speed']

model = sm.OLS(y, X).fit()
print(model.summary())

# Extract R² and partial R² contributions
total_variance = y.var()
demand_only_r2 = compute_r2_demand_only(data)
behavioral_r2 = model.rsquared - demand_only_r2

print(f"Demand alone explains: {demand_only_r2*100:.1f}% of variance")
print(f"Behavioral params add: {behavioral_r2*100:.1f}% of variance")
```

### Success Criterion (Pass/Fail Gate)

**PASS if:**
- Demand multiplier alone explains ≥95% of speed variance
- Behavioral parameters contribute <5% additional variance
- → Conclusion: Demand multiplier is the primary driver; behavioral tuning not needed

**FAIL if:**
- Behavioral parameters explain >10% additional variance
- → Conclusion: Behavioral parameters also matter; would need independent trajectory/behavioral data to map TomTom → behavior

### Decision

- **If PASS:** Skip Experiment 3; behavioral parameters are negligible; proceed to Experiment 4
- **If FAIL:** This experiment shows that state-to-demand mapping alone is insufficient; need behavioral data (e.g., GPS trajectories, lane-level occupancy) before state conditioning is defensible; **STOP and reassess**

---

## Experiment 4: Held-Out Validation

### Objective

Prove that the state-to-demand mapping derived in Experiment 2 generalizes to **unseen time periods** (not just the training period).

### Scientific Question

*Is the state-to-demand mapping stable across time? Does it apply to new data (June 16-19) as well as training data (June 11-15)?*

### Experimental Design

#### Data Split

**Training period:** June 11-15 (Task 35 training split)
- Use TomTom observations from this period to derive state-to-demand mapping

**Validation period:** June 16-19 (Task 35 validation split)
- Use TomTom observations from this period to test the mapping
- Do NOT use these observations to fit parameters

#### Workflow

```
Step 1: Retrain mapping on June 11-15 data
   → state_demand_mapping_training.csv
   
Step 2: For each June 16-19 TomTom observation:
   a. Read congestion_state (e.g., SEVERE)
   b. Look up corresponding SUMO demand multiplier from training mapping
   c. Run SUMO with that demand multiplier
   d. Measure SUMO speed, delay, throughput
   e. Compare SUMO results to TomTom observations
   
Step 3: Compute prediction error
   → held_out_validation_results.csv
   
Step 4: Analyze error for bias/systematic patterns
   → check if error depends on time-of-day, incident state, etc.
```

#### Measurements

For each June 16-19 observation:

```csv
timestamp,tomtom_state,tomtom_speed_kmh,tomtom_speed_ratio,predicted_demand_multiplier,sumo_speed_kmh,sumo_speed_ratio,speed_mape_pct,travel_time_error_pct,delay_error_pct,incident_state
2026-06-16T09:00:03,FREE_FLOW,36,1.0,0.092,35.8,0.994,0.6%,1.1%,5.3%,NO_INCIDENT
2026-06-16T12:00:02,MODERATE,31,0.861,0.193,30.2,0.839,2.5%,-3.1%,-2.7%,NO_INCIDENT
...
```

### Success Criterion (Pass/Fail Gate)

**PASS if ALL:**

1. **Mean Absolute Percentage Error (MAPE) ≤ 15% for travel time**
   ```
   MAPE = mean(|actual - predicted| / actual × 100%)
   ```

2. **MAPE ≤ 10% for speed_ratio**
   ```
   MAPE = mean(|actual_speed_ratio - predicted_speed_ratio| / actual_speed_ratio × 100%)
   ```

3. **No systematic bias:**
   - Mean error ≈ 0 (errors randomly distributed)
   - Errors do not correlate with time-of-day, incident state, or congestion level
   - Plot residuals vs. predicted: should show random scatter, not funnel or pattern

4. **Reproducibility:**
   - Re-run validation with new random seed → MAPE within ±2 percentage points

**FAIL if:**

- MAPE > 15% for travel time (mapping doesn't generalize)
- Systematic bias detected (e.g., consistent underprediction at high demand)
- Error correlates with incident state (suggests external factors dominate)

### Failure Handling

If this experiment **FAILS:**

**Possible causes:**

1. **Special events in validation period:** Incidents, road closures, construction, festivals
   - Fix: Filter validation data to remove flagged incident periods; re-run analysis
   
2. **Seasonal/behavioral change:** Validation period has different traffic patterns (e.g., weekend vs. weekday)
   - Fix: Ensure training and validation periods have similar day-of-week composition
   
3. **Mapping overfitted to training period:** June 11-15 had unusual traffic characteristics
   - Fix: Extend training period to include more diverse conditions; re-derive mapping

4. **External confounding variables:** TomTom state not determined solely by demand
   - Fix: Include incident state, time-of-day, day-of-week as covariates in model; re-fit

### Decision Gate

**Upstream manager review:** Upon completion, present:
- MAPE summary table (travel time, speed, by state)
- Error distribution plot (should be centered ~0, symmetric)
- Validation results by time-of-day and incident state
- Summary: "Mapping generalizes [WELL/POORLY] to unseen data"
- Recommendation: [PROCEED to Experiment 5] OR [STOP; mapping not stable across time]

---

## Experiment 5: Multi-State Intervention Generalization

### Objective

Prove that intervention benefits are **consistent and predictable** across all four traffic states.

### Scientific Question

*Does a single intervention mechanism (e.g., 75% adoption of Route 4) produce similar benefits in FREE_FLOW, MODERATE, CONGESTED, and SEVERE states?*

### Experimental Design

#### Workflow

For each of the four states:

```
1. Load TomTom state and demand multiplier from Experiment 2
2. Create SUMO scenario:
   - Network: auto_tool/cleaned_network.net.xml
   - Demand multiplier: state-specific (from Exp 2)
   - Intervention: fixed adoption-only route intervention (75% to Route 4)
3. Run baseline simulation (0% adoption; all vehicles on routes 1-3)
4. Run intervention simulation (75% adoption; 75% vehicles on Route 4)
5. Compare: (intervention - baseline) / baseline × 100%
```

#### Measurements

For each state, record:

```csv
state,demand_multiplier,baseline_travel_time_s,intervention_travel_time_s,baseline_speed_kmh,intervention_speed_kmh,actual_adoption_pct,travel_time_improvement_pct,speed_improvement_pct
FREE_FLOW,0.092,240.6,179.3,25.6,42.5,74.9,25.5%,66.0%
MODERATE,0.193,250.0,185.0,25.0,41.5,75.0,26.0%,66.0%
CONGESTED,0.266,270.0,195.0,21.5,37.8,75.1,27.8%,75.8%
SEVERE,0.339,290.0,210.0,19.0,33.0,74.8,27.6%,73.7%
```

### Success Criterion (Pass/Fail Gate)

**PASS if:**

1. **Consistent intervention benefit across states:**
   - Travel time improvement ≥ 0% for all states (not worse)
   - Improvement range ≤ 50 percentage points between worst and best state
   - Example: FREE_FLOW saves 25%, SEVERE saves 28% (range = 3%) ✓

2. **Monotonic or at least consistent pattern:**
   - Benefit increases or stays stable with demand level
   - OR benefit is non-monotonic but explainable (e.g., capacity saturation at SEVERE)

3. **Adoption achieves target:**
   - Actual adoption within ±2 percentage points of requested adoption (75%)

4. **No capacity saturation issues:**
   - Route 4 does not drop to zero vehicles or show signs of collapse
   - Unfinished vehicles at end of simulation <5% of total

**FAIL if:**

- Intervention produces negative benefit (worse travel time) in any state (e.g., intervention makes SEVERE congestion worse)
- High variance: improvement for same state differs >20 percentage points across runs
- Adoption fails to reach target (< 60% in any state)
- Route 4 demonstrates signs of overload/saturation

### Failure Handling

If this experiment **FAILS:**

**Possible causes:**

1. **Route 4 capacity limit exceeded in SEVERE state:**
   - High demand overloads new route; intervention counterproductive
   - Fix: Increase Route 4 capacity (e.g., 6-lane vs. 4-lane) or reduce adoption rate in SEVERE state
   
2. **Demand multiplier from Experiment 2 is incorrect for SEVERE:**
   - Predicted SEVERE demand doesn't match observed congestion
   - Fix: Re-validate state-to-demand mapping; check for outliers or special conditions

3. **Intervention mechanism (adoption-only) insufficient for SEVERE:**
   - Route diversion alone doesn't improve SEVERE traffic
   - Fix: Consider multi-mechanism interventions (widening + signals + adoption)

### Decision Gate

**Upstream manager review:** Upon completion, present:
- Multi-state intervention results table (all four states)
- Improvement plot: state vs. travel time improvement (should be ≥0 for all)
- Adoption actual vs. requested (should match closely)
- Summary: "Intervention benefits are [CONSISTENT/INCONSISTENT] across states"
- Recommendation: [DEPLOY state-conditioned SUMO with this mapping] OR [REFINE intervention or mapping]

---

## Summary: Go/No-Go Decision Tree

```
START: Can we implement physically state-conditioned SUMO?

├─→ Experiment 1: Demand Monotonicity
│   │
│   ├─ PASS → Proceed to Exp 2
│   └─ FAIL → STOP; Fix SUMO model (Task 29 recommendations); re-run Exp 1
│
├─→ Experiment 2: State-to-Demand Mapping
│   │
│   ├─ PASS + demand explains ≥95% variance → Skip Exp 3; proceed to Exp 4
│   ├─ PASS + demand explains <95% variance → Proceed to Exp 3 (optional)
│   └─ FAIL → STOP; Re-evaluate approach (change detector, adjust state definitions, etc.)
│
├─→ Experiment 3: Behavioral Sensitivity (Optional)
│   │
│   ├─ PASS (demand dominates) → Proceed to Exp 4
│   └─ FAIL (behavioral matters) → STOP; Require behavioral data (trajectories, signals)
│
├─→ Experiment 4: Held-Out Validation
│   │
│   ├─ PASS (MAPE ≤ 15%) → Proceed to Exp 5
│   └─ FAIL (MAPE > 15%) → STOP; Mapping doesn't generalize; investigate external factors
│
├─→ Experiment 5: Multi-State Intervention
│   │
│   ├─ PASS (benefit ≥0% all states, consistent) → ✓ READY FOR PRODUCTION
│   │                                               Upgrade to (A) physically state-conditioned SUMO
│   └─ FAIL (negative benefit or high variance) → STOP; Refine intervention or mapping

DECISION: If all 5 experiments PASS → Deploy state-conditioned SUMO
          If any experiment FAILS → DO NOT DEPLOY; fix root cause and retry
```

---

## Resource and Timeline Estimate

| Experiment | Effort | Duration | Cost |
|---|---|---|---|
| 1. Demand Monotonicity | 1 FTE research + compute | 1-2 weeks | Low ($500 compute) |
| 2. State-to-Demand Mapping | 1 FTE research + data acq. | 3-4 weeks | Medium ($2-5K data) |
| 3. Behavioral Sensitivity | 1 FTE (optional) | 2 weeks | Low ($300 compute) |
| 4. Held-Out Validation | 1 FTE research + compute | 2-3 weeks | Low ($500 compute) |
| 5. Multi-State Intervention | 1 FTE research + compute | 2-3 weeks | Medium ($1K compute) |
| **Total** | ~5 FTE | **~10-14 weeks** | **~$4-6.8K** |

**With team coordination, data acquisition delays:** 3-6 months wall-clock time

---

## Approval Checklist

- [ ] Upstream manager approves Experiment 1-2 plan (gate 1)
- [ ] Experiment 1 passes monotonicity test; manager approves Experiment 2 (gate 2)
- [ ] Experiment 2 passes state-to-demand mapping; manager approves Experiments 3-4 (gate 3)
- [ ] Experiment 4 passes held-out validation; manager approves Experiment 5 (gate 4)
- [ ] Experiment 5 passes multi-state intervention; manager approves production deployment (gate 5)

**Cannot skip gates. Cannot ship state-conditioned SUMO without all experiments passing.**
