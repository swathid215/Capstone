# A lineage-preserving digital-twin workflow for traffic-infrastructure intervention analysis under observed traffic-state uncertainty

## Working manuscript draft

**Target journal:** Simulation Modelling Practice and Theory  
**Article type:** Methodology and application paper  
**Status:** Draft for author review; numerical results, authorship, references, and external data access require final verification.

### Title page

**Authors:** [Author 1], [Author 2], [Author 3]  
**Affiliations:** [Institution, department, full postal address, country]  
**Corresponding author:** [Name, email, postal address]  

## Abstract

Urban infrastructure decisions require simulation models that connect observed traffic conditions with counterfactual intervention analysis without confusing the two. This paper presents the Urban Integrated Digital Twin (UIDT), a web-based workflow for Bangalore that combines historical TomTom traffic observations, OpenStreetMap network processing, SUMO-based traffic simulation, infrastructure feasibility checks, and intervention reporting. The contribution is a lineage-preserving integration pattern: provider observations are retained as an observed layer, normalized traffic-state indicators are derived deterministically, and a selected historical state is attached to a simulation scenario without being converted into an unsupported vehicle count, origin-destination matrix, or behavioral parameter. The workflow supports exact timestamp lookup, baseline-versus-intervention comparison, route-adoption sweeps, and machine-readable artifacts. A scientific defensibility audit found that the available TomTom variables did not identify a SUMO control surface. A preliminary demand sensitivity experiment was also non-monotonic: the 1.5 demand scenario produced a higher corridor speed than the baseline scenario. Accordingly, the implemented system is classified as state selection and context replay rather than physically state-conditioned SUMO. In a severe-state demonstration, the intervention run reduced mean simulated travel time from 246.04 s to 194.78 s and increased mean simulated speed from 24.91 to 40.90 km/h at approximately 75% adoption. These are SUMO-versus-SUMO counterfactual results, not validation against TomTom. The paper shows how explicit lineage and negative evidence can make an urban simulation workflow scientifically auditable while identifying the experiments required for future physical state conditioning.

**Keywords:** traffic simulation; digital twin; SUMO; data lineage; infrastructure planning; scenario analysis; Bangalore

## 1. Introduction

Traffic-infrastructure interventions are evaluated against a moving target: demand, travel time, incidents, and route choice vary across time. A digital-twin workflow can help decision makers compare a baseline network with a proposed road, flyover, tunnel, or route intervention. The scientific difficulty is that traffic observations and simulation controls are not interchangeable. A provider may report a current speed and a free-flow speed for a flow segment, while a microscopic simulator requires demand, routes, network geometry, signal timing, and vehicle-behaviour parameters.

UIDT was developed as an integrated planning workflow for Bangalore. A user draws or selects an intervention in a map-based command center. The backend retrieves demand inputs, evaluates infrastructure feasibility, modifies a network representation, executes a traffic simulation, and returns a report with maps and metrics. The repository also contains a historical traffic-state integration in which TomTom observations can be queried by exact timestamp and displayed alongside simulation results.

The central research question is: **How can historical traffic observations be connected to an intervention simulation while preserving the distinction between observed evidence, derived indicators, and simulated counterfactuals?** The paper makes three contributions:

1. It specifies and implements a reproducible lineage model for connecting TomTom observations to an urban simulation workflow without unsupported parameter injection.
2. It describes an end-to-end intervention pipeline covering demand retrieval, bottleneck heuristics, network feasibility, graph modification, route adoption, simulation, and reporting.
3. It demonstrates a defensibility gate that tests whether observed traffic states can be mapped to SUMO controls, and reports the negative result and its implications instead of presenting context replay as physical calibration.

The remainder of the paper is organized as follows. Section 2 describes the system and data lineage. Section 3 presents the simulation and intervention methodology. Section 4 reports the available experiments and results. Section 5 discusses validity, limitations, and the experiments needed for physical state conditioning. Section 6 concludes the paper.

## 2. System architecture and data lineage

### 2.1 UIDT workflow

The implemented workflow contains four phases. Phase 1 produces ward-level hourly demand inputs from the project demand pipeline. Phase 2 provides a heuristic preview based on graph bottleneck centrality and optional widening or smart-signal estimates. Phase 3 downloads or reuses an OpenStreetMap road network, checks an intervention for feasibility, and creates a modified graph. Phase 4 compares a baseline with an intervention scenario using the project simulation service, with SUMO execution where the required binaries and artifacts are available and a NetworkX fallback in the service architecture.

The principal API routes are `GET /health`, `GET /demand`, `POST /simulate`, `POST /alternatives`, and `POST /analyze`. The `/alternatives` route generates road, flyover, and tunnel candidates and ranks them after feasibility and route checks. The `/analyze` route combines the phase outputs into a report suitable for the frontend command center.

### 2.2 Observed and derived traffic state

The historical traffic-state layer is derived from 161 TomTom captures collected between 11 and 19 June 2026, according to the repository audit inventory. Each capture contains a timestamp, a flow-segment response, and incident records. The flow geometry contains 802 coordinate points and is invariant across captures, indicating one repeated flow-segment representation rather than 802 independent observed road segments.

For each observation, UIDT derives normalized indicators:

\[
 r_v = \frac{v_{current}}{v_{free}}, \qquad L_v = 1-r_v
\]

\[
 r_t = \frac{t_{current}}{t_{free}}, \qquad D_t = r_t-1
\]

where $r_v$ is the speed ratio, $L_v$ is speed loss, $r_t$ is the travel-time ratio, and $D_t$ is the delay ratio. A four-level label, `FREE_FLOW`, `MODERATE`, `CONGESTED`, or `SEVERE`, is assigned through the documented quartile-based state procedure. Incident state and provider provenance are retained as separate fields.

The service exposes all states, the latest state, and exact timestamp lookup. It does not interpolate missing observations, pad time series, accept SUMO configuration input, or return a vehicle count or SUMO parameter. This makes the lookup deterministic and keeps the provider observation separate from the simulation model.

### 2.3 Lineage contract

UIDT uses three explicit layers:

- **Observed:** TomTom current speed, free-flow speed, travel times, incidents, geometry, and timestamp.
- **Derived:** speed ratio, speed loss, travel-time ratio, delay ratio, congestion state, and incident state.
- **Simulated:** network, route file, demand realization, route adoption, travel time, speed, throughput, and intervention benefit.

The selected historical state provides temporal and contextual information. It does not modify the SUMO network, demand, route file, signal plan, or vehicle parameters. This boundary is important because TomTom observations in the available dataset do not include vehicle counts, origin-destination demand, lane-level occupancy, signal phases, per-edge measurements, or vehicle trajectories.

## 3. Simulation and intervention methodology

### 3.1 Network and route representation

The principal SUMO network is `auto_tool/cleaned_network.net.xml`, with a UTM Zone 43 projection and a WGS84 extent approximately spanning 77.512775 to 77.599831 degrees longitude and 12.911718 to 12.988030 degrees latitude. The network includes residential, tertiary, secondary, trunk, service, and path road types, with one- and two-lane edges and nominal speeds from 10 to 100 km/h.

The route artifacts contain vehicles with departure times expressed in seconds from midnight and space-separated SUMO edge sequences. The documented full-day configuration runs from 07:00 to 24:00. Calibration artifacts use a 900 s warm-up and a 900-3600 s evaluation window. The production simulation service uses a committed baseline route file and may construct an intervention route set that diverts a selected fraction of vehicles to an alternative route.

### 3.2 Infrastructure and route intervention

The user supplies an origin, destination, intervention type, geometry, road width, and directionality. Phase 3 checks clearances, overlaps, and collisions before modifying the graph. The simulation comparison then holds the baseline demand context fixed and varies intervention adoption. At an adoption rate of 75%, the intervention assigns approximately 75% of demand to the alternative route while preserving the total demand volume. The resulting comparison is:

\[
 I_{TT}=\frac{T_{baseline}-T_{intervention}}{T_{baseline}}\times100
\]

\[
 I_{V}=\frac{V_{intervention}-V_{baseline}}{V_{baseline}}\times100
\]

where $T$ is mean travel time and $V$ is mean speed. These quantities measure simulated counterfactual change only.

### 3.3 Demand sensitivity and identifiability gate

Before using an observed state as a physical SUMO condition, the workflow requires evidence that a candidate SUMO control has a stable relationship with traffic-state metrics. Task 28 tested low, baseline, and high demand scenarios with 2,725, 5,450, and 8,175 vehicles, respectively, on a six-edge corridor. The observed response was non-monotonic: the high-demand scenario had a corridor speed of 34.97 km/h, exceeding the baseline value of 30.87 km/h. Therefore, a demand multiplier could not be estimated defensibly from the available experiment.

A subsequent control-surface audit examined demand, origin-destination demand, desired speed, car-following parameters, acceleration, minimum gap, lane changing, signals, speed limits, routing, and insertion timing. None was identifiable from the available TomTom fields. Task 38 consequently recorded `NOT_IDENTIFIABLE` for all four traffic states and withheld physical state-conditioned SUMO runs.

## 4. Results

### 4.1 End-to-end service validation

The repository audit reports successful checks for the core service workflow: `/health` returned HTTP 200; latest-state retrieval returned 161 historical states; exact timestamp lookup returned a state for a valid timestamp and 404 for an invalid or empty lookup; and the lineage labels were preserved. The audit also confirmed that the state service did not inject state-conditioned demand or alter SUMO parameters.

### 4.2 External BTP validation protocol

The implementation now exposes a deterministic validation endpoint, `POST /api/validation/btp`, and a corresponding Traffic Report upload panel. It accepts paired BTP and simulation observations and reports the number of complete pairs, excluded records, mean absolute error, root mean square error, mean absolute percentage error, and signed bias separately for speed and travel time. Missing or invalid pairs are excluded and counted; they are not imputed.

At the time of this draft, the BTP dataset has not been committed to the repository and no BTP numerical validation result is claimed here. The required schema, corridor/time alignment procedure, provenance fields, and pre-submission validation conditions are specified in `docs/BTP_VALIDATION_PROTOCOL.md`. Once the BTP data access and alignment are verified, the final paper should add a held-out validation table with confidence intervals and the exact dataset-access statement.

### 4.3 Historical severe-state intervention demonstration

One demonstration selected a historical severe observation at approximately 19:00 on 13 June 2026 and ran the existing baseline/intervention workflow at 75% adoption. The results were:

| Metric | Baseline | Intervention | Change |
|---|---:|---:|---:|
| Mean travel time (s) | 246.04 | 194.78 | -51.26 s (-20.83%) |
| Mean speed (km/h) | 24.91 | 40.90 | +15.99 (+64.19%) |
| Actual adoption | 0% | 74.9% | — |

The state label identifies the historical context in which the workflow was initiated. It does not validate the simulated baseline against the TomTom value and does not imply that the intervention would produce the same benefit in the observed road system.

### 4.4 Multi-state result validity

A later artifact reported four state labels with baseline and intervention values. The values for `MODERATE` and `CONGESTED` were identical: 250.56 s versus 187.03 s and 25.73 km/h versus 42.25 km/h. The audit traced this to identical simulation contexts and the absence of a state-conditioned demand or behavioural branch. Earlier documentation had marked those states as not run.

Accordingly, the four-row artifact is not treated here as evidence of four physically distinct traffic-state simulations. It demonstrates that multiple observed labels can be attached to the same fixed simulation context, and it exposes why state labels must not be interpreted as simulator conditions without a verified mapping.

## 5. Discussion

### 5.1 Scientific contribution

The main result is methodological rather than a claim of calibrated traffic prediction. UIDT provides a practical way to place historical traffic evidence beside a counterfactual simulation while preserving the provenance of every quantity. This distinction is useful for digital twins because an apparently precise observed speed ratio does not identify the latent causes that a microscopic simulator needs. Treating it as a demand multiplier would create a result that is computationally reproducible but scientifically unsupported.

The negative identifiability result is also operationally useful. It prevents arbitrary parameter changes from being presented as state conditioning and creates a concrete agenda for further data collection. The workflow can therefore support decision exploration under explicit uncertainty, provided its output is described as a simulated scenario comparison rather than a validated prediction of real-world effects.

### 5.2 Limitations and threats to validity

First, the TomTom observations are external to this repository and have naive local timestamps without timezone metadata. Their sampling has a median interval of approximately one hour, 48 missing hour slots, and a maximum gap of approximately 22.8 hours. Exact lookup avoids inventing intermediate states but does not solve temporal coverage.

Second, the TomTom flow geometry extends beyond the SUMO network in the north-south direction, and the repeated 802-point geometry is not an edge-level observation. A complete spatial validation requires a documented map-matching procedure and verified edge correspondence.

Third, the demand sensitivity experiment has too few scenarios and exhibits a non-monotonic response. The result is sufficient to block unsupported mapping, but not sufficient to characterize the simulator's full demand response. The route artifacts also contain single-edge stub vehicles and require further integrity checks.

Fourth, the reported intervention results are SUMO-versus-SUMO results. No independent detector counts, trajectory data, signal timing, or held-out traffic measurements were available to validate the simulated values. The frontend, artifact-based run records, and failure paths also require additional production hardening.

Finally, the current Phase 2 optimization is a heuristic preview rather than a complete optimization engine, and optional Gemini suggestions are not required for the core workflow. These components should not be presented as validated optimization or causal infrastructure recommendations.

### 5.3 Experiments required for physical state conditioning

Moving from context replay to physical state conditioning requires at least five gates:

1. Re-run demand sensitivity with 0.5x, 0.75x, 1.0x, 1.25x, and 1.5x demand, five replicates per scenario, fixed seeds and route ordering, and corridor-level confidence intervals.
2. Align TomTom states with independent detector counts or trajectory data and estimate a state-to-demand mapping, with a pre-specified error threshold.
3. If demand does not explain the observed variation, test behavioural and signal parameters using measurements that identify those controls.
4. Validate the selected mapping on held-out observations using travel-time and speed error thresholds.
5. Run complete baseline/intervention pairs for all four states and report uncertainty, adoption, insertion success, and failure cases.

Until these gates pass, the appropriate implementation classification remains **state selection/context only**.

## 6. Conclusions

This paper presented UIDT, a Bangalore-focused workflow for connecting historical traffic observations to infrastructure intervention simulation. Its defining feature is a strict data-lineage boundary: TomTom observations remain observed evidence, normalized state indicators are deterministic derivatives, and SUMO outputs remain simulated counterfactuals. The available evidence did not identify a valid mapping from the four observed traffic states to SUMO demand or behavioural controls. A non-monotonic demand experiment and duplicated multi-state contexts reinforce that conclusion.

The workflow nevertheless supports useful and transparent analysis. It can select an exact historical observation, evaluate an intervention, run a baseline-versus-adoption comparison, and report the result without claiming that the simulation reproduces or predicts the real-world state. Future work should acquire independent traffic measurements, repair and repeat the demand-response experiments, validate spatial and temporal alignment, and only then consider physical state-conditioned SUMO.

## Declarations

### Funding

This research did not receive any specific grant from funding agencies in the public, commercial, or not-for-profit sectors. **[Verify or replace.]**

### Declaration of competing interest

The authors declare that they have no known competing financial interests or personal relationships that could have appeared to influence the work reported in this paper. **[Verify.]**

### Data availability

The repository contains source code, SUMO network and route artifacts, calibration outputs, and audit documentation. The external TomTom observation cache used by the audit is not included in this repository. The authors should deposit the shareable observation data, derived state table, simulation inputs, and result artifacts in a suitable repository and provide a persistent identifier. If the provider data cannot be redistributed, the final submission should state the access restriction and provide the derived, non-sensitive materials and exact extraction procedure.

### Declaration of generative AI and AI-assisted technologies in the manuscript preparation process

During the preparation of this manuscript, the authors used GitHub Copilot to support organization and language drafting. After using this tool, the authors reviewed and edited the content as needed and take full responsibility for the content of the published article.

### Acknowledgements

[Add acknowledgements for contributors, data providers, and infrastructure support.]

## References

[1] Eclipse SUMO, Simulation of Urban MObility, software documentation and source repository. https://eclipse.dev/sumo/ (accessed 28 August 2026).

[2] OpenStreetMap contributors, Planet dump and map data. https://www.openstreetmap.org/ (accessed 28 August 2026).

[3] UIDT project, `README.md`, repository documentation, 2026.

[4] UIDT project, `AUDIT_STATE_CONDITIONING_SCIENTIFIC_DEFENSIBILITY.md`, repository audit report, 2026.

[5] UIDT project, `DATA_LINEAGE_STATE_CONDITIONING_DIAGRAM.md`, repository design documentation, 2026.

[6] UIDT project, `MINIMUM_EXPERIMENTS_FOR_STATE_CONDITIONING.md`, repository experiment protocol, 2026.

[7] TomTom, Traffic Flow and Incidents APIs, provider documentation. **[Insert exact version, URL, and access date if permitted.]**

## Author checklist before submission

- Replace all author, affiliation, funding, and acknowledgement placeholders.
- Verify every numerical result against raw executable artifacts.
- Add the exact TomTom dataset citation or a data-access restriction statement.
- Add literature on digital twins, traffic-state estimation, microscopic simulation, and intervention evaluation.
- Confirm whether each reported run used native SUMO or the NetworkX fallback.
- Deposit figures separately and cite each figure and table in the final manuscript.
- Reconcile the journal's single-column editable-source requirement with the final Word or LaTeX submission.
- Submit 3-5 highlights as a separate editable file; each bullet must be at most 85 characters.
