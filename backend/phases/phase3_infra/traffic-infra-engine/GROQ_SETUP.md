# Groq Integration - Quick Start

## What Changed

✅ **OpenAI GPT-4** → **Groq Mixtral** (5-10x faster, free tier)

## Setup (3 Steps)

### 1. Get API Key
```
Go to: https://console.groq.com/
Sign up → Create API key
Copy the key (starts with gsk_)
```

### 2. Configure Environment
```bash
# Copy template
cp .env.example .env

# Edit .env and paste your key
GROQ_API_KEY=gsk_your_key_here
```

### 3. Install & Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Why Groq?

| Feature | Groq | OpenAI |
|---------|------|--------|
| **Cost** | Free (30 req/min) | Paid ($0.10-0.15/call) |
| **Speed** | 5-10x faster | Baseline |
| **Sign-up** | Instant | Credit card needed |
| **Model** | Mixtral 8x7B, Llama 2 | GPT-4, GPT-3.5 |

## API Key Format

✅ Valid: `gsk_abcdef123456...`
❌ Invalid: Missing `gsk_` prefix or extra spaces

## Troubleshooting

### "groq library not installed"
```bash
pip install groq>=0.4.0
```

### "Invalid API key"
- Check `.env` file has no spaces
- Verify key starts with `gsk_`
- Key not expired on Groq Console

### Fallback Behavior
- No API key? → Uses heuristic mode (works offline!)
- API fails? → Automatically falls back to rules

## Files Modified

1. `smart_architect.py` - Groq client initialization + API calls
2. `requirements.txt` - groq instead of openai
3. `.env.example` - GROQ_API_KEY template
4. `app.py` - Load GROQ_API_KEY instead of OPENAI_API_KEY
5. Documentation files - Updated guides

## Available Models on Groq

Switch models in `smart_architect.py` line 81:

```python
model="mixtral-8x7b-32768"    # Balanced (default)
model="llama2-70b-4096"        # Better reasoning
model="gemma-7b-it"            # Fastest
```

---

**Status**: ✅ All Groq integration complete. Ready to run!
