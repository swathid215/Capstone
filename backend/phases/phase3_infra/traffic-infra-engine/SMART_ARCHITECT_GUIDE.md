# Smart Architect Engine - Setup & Usage Guide

## Overview

The **Smart Architect Engine** is an AI-powered infrastructure recommendation system that analyzes multiple parameters and provides intelligent recommendations for infrastructure types (Surface Road, Flyover, Tunnel, etc.).

## Features

### 1. **Multi-Parameter Analysis**
The engine analyzes the following parameters:
- **Route Geometry**: LineString (roads) or Polygon (areas)
- **Route Length**: Total length/perimeter in meters
- **Building Collisions**: Number of structures intersecting the route
- **Collision Density**: Building concentration per 100m
- **Network Connectivity**: Impact on traffic network connections
- **Urban Density**: Area classification (Rural → Dense Urban)
- **Route Efficiency**: Straight-line vs actual path ratio
- **Land Acquisition Complexity**: Based on collision count

### 2. **Infrastructure Recommendations**
The engine recommends one of:
- **Surface Road**: Ground-level standard road construction
- **Flyover / Elevated Corridor**: Grade-separated elevated structure
- **Tunnel / Underground Bypass**: Below-ground infrastructure
- **Mixed Solution**: Combination approaches for complex scenarios
- **Area Redevelopment**: For polygon-based projects

### 3. **Detailed Output**
Each recommendation includes:
- ✅ **Recommendation**: Optimal infrastructure type
- 📝 **Reasoning**: Detailed explanation (cost, benefits, tradeoffs)
- 💰 **Cost Estimate**: Low → Very High range
- ⏱️ **Implementation Timeline**: Estimated duration in months
- 🏗️ **Physical Metrics**: Geometry, connectivity, efficiency scores
- ⚠️ **Risk Factors**: Key implementation challenges
- 🌍 **Environmental Impact**: Low → High assessment
- 📊 **Confidence Level**: 0-100% recommendation confidence

## Setup Instructions

### Option 1: With Groq API (LLM Mode - Recommended)

#### 1. Get Groq API Key
1. Visit [Groq Console](https://console.groq.com/)
2. Sign up for a free account
3. Navigate to "API keys" section
4. Create an API key
5. Copy the key (keep it secret!)

#### 2. Configure Environment Variable

Create a `.env` file in the `traffic-infra-engine/` directory:

```bash
# Copy the example file
cp .env.example .env

# Edit .env and add your Groq API key
# On Windows (PowerShell):
code .env
```

Add your key:
```
GROQ_API_KEY=gsk_your-actual-key-here
```

#### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

This will install:
- `groq>=0.4.0` - Groq Python SDK
- `python-dotenv>=0.21.0` - Environment variable loader

#### 4. Run the App
```bash
streamlit run app.py
```

**Note**: Groq offers a **free tier** with generous rate limits, making it cost-effective for development and testing!

### Option 2: Without API Key (Heuristic Mode)

If you don't have a Groq API key, the engine automatically falls back to **heuristic mode**:

```bash
# Simply run without setting GROQ_API_KEY
streamlit run app.py
```

The app will use rule-based logic instead of AI, but still provides solid recommendations.

## How It Works

### Analysis Flow

1. **User draws geometry** on the Smart Architect map
2. **User clicks "Get Smart Suggestion"** button
3. **Feasibility Engine** checks building collisions
4. **Smart Architect Engine** analyzes metrics:
   - If API key present → Sends metrics to Groq Mixtral for analysis
   - If no API key → Applies rule-based heuristics
5. **Engine returns** infrastructure recommendation with detailed reasoning
6. **UI displays**:
   - 📊 Infrastructure type and justification
   - 💰 Cost and timeline estimates
   - 📈 Detailed metrics and efficiency scores
   - ⚠️ Risk factors and environmental impact
   - 🏢 Collision map (if applicable)

### Decision Logic (Heuristic Mode)

The heuristic system uses thresholds based on:

#### LineString (Road Routes)
| Building Collisions | Length | Recommendation |
|---|---|---|
| > 10 | Any | Tunnel (90% confidence) |
| 4-10 | > 1500m | Tunnel (82%) or Flyover (85%) |
| 4-10 | < 1500m | Flyover (85%) |
| 1-3 | Any | Flyover (80%) |
| 0 | > 2000m | Arterial Road / Highway (95%) |
| 0 | 500-2000m | Arterial Road (95%) |
| 0 | < 500m | Local Road Widening (95%) |

#### Polygon (Area Projects)
| Building Collisions | Recommendation |
|---|---|
| > 8 | Area Redevelopment (80%) |
| 0-8 | New Layout / Hub (90%) |

## Example Usage

### Scenario 1: Urban Corridor (High Collision Density)

**Input:**
- Draw 1.5 km road through city center
- 8 building collisions detected

**Output:**
```
🏗️ FLYOVER / ELEVATED CORRIDOR

Route length 1500m with 8 building obstacles in moderately dense area.
Elevated grade separation is cost-effective, avoids mass demolition, and provides 
highway-speed connectivity. Connectivity gain of 45.2 points justifies elevated 
infrastructure. Ground-level urban vitality is preserved.

💰 Cost: High | ⏱️ Timeline: 12-18 months | 📊 Confidence: 85%
⚠️ Risks: Structural design for span, Aesthetic integration
🌍 Environmental Impact: Moderate
```

### Scenario 2: Rural Bypass Road

**Input:**
- Draw 3 km straight road
- 0 building collisions

**Output:**
```
🏗️ ARTERIAL ROAD / HIGHWAY EXPANSION

Clear path of 3000m with zero building interference through rural area.
Standard multi-lane arterial road is most cost-effective. Excellent for 
inter-city connectivity with 72.5/100 connectivity potential.

💰 Cost: Medium | ⏱️ Timeline: 8-12 months | 📊 Confidence: 95%
⚠️ Risks: Land rights acquisition, Drainage design
🌍 Environmental Impact: Low
```

### Scenario 3: Dense Urban Redevelopment

**Input:**
- Draw polygon area (2 hectares)
- 15 buildings within

**Output:**
```
🏗️ AREA REDEVELOPMENT / MIXED-USE COMPLEX

Large area with 15 existing structures presents significant development opportunity.
Phased land assembly and strategic demolition can enable modern development.
Infrastructure integration supports multi-modal transportation hub.

💰 Cost: Very High | ⏱️ Timeline: 36-60 months | 📊 Confidence: 80%
⚠️ Risks: Complex land acquisition, Stakeholder negotiations
🌍 Environmental Impact: High (Long-term positive)
```

## Parameters Explained

### Collision Density
```
Collisions per 100 meters of route
Lower = Rural/sparse area
Higher = Urban/dense area
```

### Connectivity Score
```
Score (0-100): Impact on traffic network
Formula: (New Connections / Route Length) × 10
80+: Excellent connectivity gain
50-79: Good network improvement
<50: Localized impact
```

### Route Efficiency
```
Ratio (0.0-1.0): Straight vs actual path
1.0 = Perfectly straight
0.5 = Twisting, indirect route
Affects cost and construction complexity
```

### Urban Density Classifications
- **Low (Rural/Open)**: 0 collisions
- **Low-Medium**: 1-3 collisions
- **Medium (Mixed)**: 4-10 collisions
- **High (Urban)**: 11-20 collisions
- **Very High (Dense Urban)**: 20+ collisions

## API Cost Considerations

### Groq API Pricing (As of 2024)
- **Free Tier**: Includes generous free limits
  - 30 requests per minute
  - Perfect for testing and development
  - No credit card required for sign-up
- **Pro Plans**: Available for production use
  - Significantly cheaper than OpenAI
  - Fast inference (5-10x faster than OpenAI)

### Cost Optimization
1. Free tier covers most development scenarios
2. Call `analyze_infrastructure()` once per geometry
3. Reuse recommendation results
4. Cache results in Streamlit session state
5. Groq's speed means fewer aggressive caching needs

## Troubleshooting

### Issue: "Groq library not installed"
```bash
pip install groq>=0.4.0
```

### Issue: "Invalid API key" error
1. Verify API key in `.env` file (should start with `gsk_`)
2. Ensure no extra spaces/newlines in `.env`
3. Check key hasn't expired on [Groq Console](https://console.groq.com/)
4. Verify key has appropriate permissions

### Issue: Recommendation doesn't load
1. Check Streamlit logs in terminal
2. Verify geometry is valid (at least 2 points)
3. Try smaller region if timeout occurs
4. Falls back to heuristic if API fails

### Issue: Want to switch to a different Groq model
Edit [smart_architect.py](smart_architect.py#L81):
```python
message = self.groq_client.chat.completions.create(
    model="llama2-70b-4096",  # Change from "mixtral-8x7b-32768"
    ...
```

Available Groq models:
- `mixtral-8x7b-32768` (default) - Balanced speed/quality
- `llama2-70b-4096` - Higher quality reasoning
- `gemma-7b-it` - Fastest inference
)
```

Available Groq models:
- `mixtral-8x7b-32768` (default) - Balanced speed/quality
- `llama2-70b-4096` - Higher quality reasoning
- `gemma-7b-it` - Fastest inference

## Architecture Diagram

```
User Interface (Streamlit)
    ↓
SmartArchitectEngine.__init__
    ├─ Check GROQ_API_KEY
    ├─ Load Groq client (if key exists)
    └─ Set use_llm flag
    ↓
SmartArchitectEngine.analyze_infrastructure()
    ├─ _calculate_metrics()
    │   ├─ Geometry analysis
    │   ├─ Collision density
    │   ├─ Connectivity score
    │   ├─ Urban density assessment
    │   └─ Route efficiency
    │
    ├─ Decision Path
    │   ├─ If use_llm:
    │   │   ├─ _build_analysis_prompt()
    │   │   ├─ Groq Mixtral API call
    │   │   ├─ _parse_llm_response()
    │   │   └─ Return structured recommendation
    │   │
    │   └─ Else:
    │       └─ _get_heuristic_recommendation()
    │           ├─ LineString logic
    │           ├─ Polygon logic
    │           └─ Return structured recommendation
    │
    └─ Return complete analysis dict
         ├─ metrics
         ├─ recommendation
         ├─ reasoning
         ├─ confidence
         ├─ cost_estimate
         ├─ implementation_time
         ├─ risk_factors
         └─ environmental_impact
```

## Advanced Configuration

### Using Environment Variables Programmatically
```python
# In app.py or custom scripts
import os
from smart_suggestion_engine import SmartArchitectEngine

api_key = os.getenv("GROQ_API_KEY")
architect = SmartArchitectEngine(api_key=api_key)
```

### Custom Cost/Timeline Estimates
Edit the recommendation logic in `smart_architect.py` to add custom business rules:

```python
def _get_heuristic_recommendation(self, metrics):
    recommendation["cost_estimate"] = "Your Custom Logic"
    recommendation["implementation_time"] = "Custom Timeline"
    return recommendation
```

### Extending with Additional Metrics
```python
# In smart_architect.py, add to _calculate_metrics():
metrics["your_custom_metric"] = custom_calculation()
```

## Support & Feedback

For issues or feature requests:
1. Check the Troubleshooting section above
2. Review OpenAI API documentation
3. Check Streamlit logs in the terminal running the app

---

**Version**: 1.0  
**Last Updated**: Apr 2026  
**Compatibility**: Python 3.8+, Streamlit 1.20+
