# Ultimate Predictions (Flask + Alpha Vantage)

A tiny Flask web app that fetches **BSE daily data** from Alpha Vantage on the server (API key stays hidden in env)
and detects **real block patterns** (Double Top/Bottom, Head & Shoulders, Triangles, Wedges, Flags, Channels, Rectangle).
Client calls `/api/analyze?symbol=RELIANCE.BSE` and renders a sparkline + metrics.

## Deploy on Render
1. Create a new **Web Service** and connect/upload this folder.
2. Environment variable:
   - `ALPHA_VANTAGE_API_KEY` = your Alpha Vantage key
3. Start command: `gunicorn app:app`
4. Render will set `PORT`; the app also defaults to port 10000 for local runs.

## Run locally
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export ALPHA_VANTAGE_API_KEY=YOUR_KEY
python app.py
# open http://localhost:10000
```

## Notes
- Simple 60s cache helps avoid free-tier rate limits.
- Server computes the pattern detection; client only renders.