# Phase1_Tanisha

This folder contains the traffic-analysis work for Bangalore ward-level travel-time data. It includes:

- the raw datasets used for exploration,
- the Jupyter notebook where the main analysis was performed,
- a Python pipeline created to reproduce a simplified version of that analysis for automation,
- and a small visualizer that turns the pipeline output into an interactive map.

The notebook is the main source of truth for the analysis logic. The Python pipeline is an automation-oriented approximation meant to support an `n8n` workflow and downstream integration.

## Folder Contents

- `ward.ipynb`
  Main exploratory and analytical notebook.

- `bangalore-wards-2019-3-OnlyWeekdays-HourlyAggregate.csv`
  Hourly aggregated weekday OD travel-time dataset for Bangalore wards.

- `bangalore_wards.json`
  GeoJSON ward boundary file with ward metadata such as `WARD_NO`, `WARD_NAME`, and `MOVEMENT_ID`.

- `tanisha_pipeline.py`
  Python script that generates a simplified synthetic demand output for automation/integration.

- `tanisha_visualizer.py`
  Folium-based map generator for the pipeline output.

## Datasets

### 1. `bangalore-wards-2019-3-OnlyWeekdays-HourlyAggregate.csv`

This is the main analytical dataset used throughout the notebook.

Observed structure:

- ~818,262 rows
- 7 columns:
  - `sourceid`
  - `dstid`
  - `hod`
  - `mean_travel_time`
  - `standard_deviation_travel_time`
  - `geometric_mean_travel_time`
  - `geometric_standard_deviation_travel_time`

Interpretation:

- `sourceid` and `dstid` represent origin-destination movement IDs.
- `hod` is the hour of day (`0`-`23`).
- travel times are recorded in seconds.

### 2. `bangalore_wards.json`

This GeoJSON provides the ward geometries and identifiers needed to connect the travel-time data to spatial wards on the map.

Important properties used in the notebook/scripts:

- `WARD_NO`
- `WARD_NAME`
- `MOVEMENT_ID`
- `DISPLAY_NAME`

There are 198 ward features in this file.

## What `ward.ipynb` Does

The notebook is not just a map notebook; it moves through several stages of analysis.

### 1. Load and inspect the datasets

The notebook:

- reads the Bangalore hourly aggregate CSV using `pandas`,
- reads the ward GeoJSON using `geopandas`,
- inspects the ward properties to understand the key fields needed for joining.

### 2. Map a ward number to the movement ID used in the CSV

This is a key step in the notebook.

The notebook focuses on **Ward No. 163 = Katriguppe**, but it does **not** use `163` directly as the OD ID in the CSV. Instead, it looks up the corresponding `MOVEMENT_ID` from the GeoJSON:

- `WARD_NO = 163`
- `WARD_NAME = Katriguppe`
- `MOVEMENT_ID = 145`

This mapping matters because the CSV uses movement IDs, not ward numbers.

### 3. Build a 9 AM accessibility / isochrone analysis

The notebook filters trips for a target ward at 9 AM, merges them with ward geometries, projects the data to `EPSG:32643`, and then:

- identifies areas reachable within 30 minutes,
- dissolves those polygons into a single isochrone shape,
- visualizes the travel-time gradient,
- calculates the total reachable area in square kilometers.

### 4. Compare time windows

The notebook compares accessibility at:

- `9 AM` (rush hour),
- `11 PM` (late night),

to show how reachable area changes based on congestion conditions.

### 5. Identify city-wide traffic extremes

Using the full Bangalore dataset, the notebook computes average travel time by hour of day and finds:

- the most congested hour,
- the least congested hour,
- and plots the city’s hourly travel pulse.

### 6. Analyze one target ward across all hours

For the selected destination ward, the notebook calculates hourly mean travel time and identifies:

- the best hour to arrive,
- the worst hour to arrive,
- and visualizes the ward-specific traffic pulse.

### 7. Rank hard-to-reach wards at a chosen hour

The notebook computes average destination accessibility at a selected hour and identifies:

- the hardest wards to reach,
- the easiest wards to reach,
- and builds a heatmap of accessibility across Bangalore.

### 8. Explore OD flows and commute patterns

The notebook also looks at:

- longest commutes into the target ward,
- top destination wards,
- top specific origin-destination corridors,
- and ward-level travel relationships visualized interactively with `folium`.

### 9. Create synthetic demand from travel-time data

Because the dataset does not contain actual vehicle counts, the notebook derives a proxy demand model:

- compute distance between ward centroids,
- estimate average speed,
- remove impossible speeds,
- use inverse travel time as a demand weight,
- optionally improve the weight with destination popularity,
- normalize to probabilities,
- stochastically sample a fixed total number of vehicles (`10,000` in the notebook),
- assign synthetic `trip_count` values to OD pairs.

This is the notebook’s most important bridge from analysis to simulation-style demand generation.

## What `tanisha_pipeline.py` Does

`tanisha_pipeline.py` is a compact automation script intended to do a simplified version of the notebook workflow so it can be triggered programmatically, such as from `n8n`.

### Pipeline behavior

The script:

1. loads the hourly aggregate CSV,
2. filters to a target hour (default `9`),
3. filters OD rows connected to Ward `163`,
4. converts `mean_travel_time` from seconds to minutes,
5. groups by `sourceid` and `dstid`,
6. computes a simplified synthetic trip score:

`synthetic_trip_count = (100 / mean_travel_min) * congestion_multiplier`

7. sorts the top 10 OD pairs,
8. enriches them with ward names from the GeoJSON,
9. writes the result as JSON to:

`../data_shared/integration_demand.json`

### Output structure

The generated JSON contains:

- `scenario_metadata`
  - congestion multiplier
  - text description
  - target hour

- `demand_matrix`
  - `sourceid`
  - `sourcename`
  - `dstid`
  - `dstname`
  - `synthetic_trip_count`
  - `travel_time_min`

### Intended use

This script is suitable when you need:

- a lightweight, repeatable demand-generation step,
- a JSON output for downstream systems,
- a simple automation hook for `n8n`,
- and a smaller artifact than the full notebook workflow.

## What `tanisha_visualizer.py` Does

The visualizer reads:

- `../data_shared/integration_demand.json`

and:

- loads ward centroids from `bangalore_wards.json`,
- creates a dark-themed `folium` map,
- draws OD flow lines between source and destination wards,
- adds hover labels with ward names and trip counts,
- saves the map to:

`../data_shared/traffic_map.html`

This is useful as a quick visual output for the automation pipeline.

## Notebook vs Pipeline

The notebook and pipeline are related, but they are not equivalent.

### The notebook is richer

The notebook includes:

- geospatial joins,
- isochrone generation,
- area calculations,
- hourly congestion analysis,
- accessibility heatmaps,
- distance and speed derivation,
- outlier cleaning,
- stochastic demand simulation.

### The pipeline is simplified

The Python script keeps only a narrow slice of the idea:

- hour-based filtering,
- corridor extraction,
- simple inverse-time demand scoring,
- top-10 ranking,
- JSON export for integration.

It is best described as a simplified operationalization of the notebook, not a full conversion of it.

## Important Current Limitation

There is one important difference between the notebook logic and the current Python pipeline:

- In `ward.ipynb`, Ward No. `163` (Katriguppe) is first mapped to `MOVEMENT_ID = 145`, and the CSV filters are applied using that movement ID.
- In `tanisha_pipeline.py`, the filter is applied directly to `sourceid == 163` or `dstid == 163`.

That means the current pipeline is **not perfectly aligned** with the notebook’s ward-selection logic.

If the automation is intended to reproduce the notebook more faithfully, this mapping should be handled explicitly in the script before filtering the CSV.

## External Output Expectation

Both Python scripts write outputs outside this folder:

- `../data_shared/integration_demand.json`
- `../data_shared/traffic_map.html`

So the parent-level `data_shared` directory is expected by the automation workflow. If it does not exist, the pipeline attempts to create it.

## Python Dependencies

### Notebook dependencies

The notebook uses:

- `pandas`
- `geopandas`
- `matplotlib`
- `seaborn`
- `folium`
- `shapely`
- `ipywidgets`

### Script dependencies

`tanisha_pipeline.py` uses:

- `pandas`
- `json`
- `os`
- `sys`

`tanisha_visualizer.py` uses:

- `folium`
- `shapely`
- `json`
- `os`

## Running the Scripts

### Generate synthetic demand JSON

```bash
python3 tanisha_pipeline.py
```

Optional congestion multiplier:

```bash
python3 tanisha_pipeline.py 1.5
```

### Generate the HTML traffic map

```bash
python3 tanisha_visualizer.py
```

## Recommended Interpretation of This Folder

This folder represents a progression:

1. **Exploratory analysis in `ward.ipynb`**
2. **Simplified demand extraction in `tanisha_pipeline.py`**
3. **Visualization / downstream integration in `tanisha_visualizer.py`**
4. **Automation intent through `n8n`**

So if someone is new to this folder:

- start with `ward.ipynb` to understand the analysis logic,
- use the Python scripts for lightweight automation,
- and treat the pipeline as an approximation of the notebook rather than a one-to-one reproduction.
