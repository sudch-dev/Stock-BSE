
import os
import requests
from flask import Flask, jsonify, request, render_template_string

API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY") or os.environ.get("API_KEY")
BASE_URL = "https://www.alphavantage.co/query"

app = Flask(__name__)

INDEX_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Alpha Vantage Live Quote</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px; background: #f4f6f8; }
      h1 { margin-top: 0; }
      form, .card { background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,.06); }
      input, button { padding: 10px 12px; border-radius: 6px; border: 1px solid #cfd8dc; }
      input { width: 220px; }
      button { background: #1a237e; color: #fff; border: none; cursor: pointer; }
      button:hover { filter: brightness(1.05); }
      .muted { color: #607d8b; font-size: 14px; }
      pre { white-space: pre-wrap; word-break: break-word; }
    </style>
  </head>
  <body>
    <h1>Alpha Vantage Live Quote</h1>
    <form method="GET" action="/quote" class="card">
      <label for="symbol">Symbol:</label>
      <input id="symbol" name="symbol" placeholder="e.g., SBIN.BSE or TCS.NS" value="RELIANCE.BSE" />
      <button type="submit">Get Quote</button>
      <div class="muted" style="margin-top:8px;">
        Uses GLOBAL_QUOTE (no historical). Set env: <code>ALPHA_VANTAGE_API_KEY</code>.
      </div>
    </form>

    <div class="card" style="margin-top:16px;">
      <div class="muted">API endpoint:</div>
      <code>/quote?symbol=SBIN.BSE</code>
    </div>
  </body>
</html>
"""

def fetch_live_quote(symbol: str):
    if not API_KEY:
        return {"error": "Missing ALPHA_VANTAGE_API_KEY (or API_KEY) in environment."}

    params = {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": API_KEY}
    try:
        r = requests.get(BASE_URL, params=params, timeout=15)
        data = r.json()
    except Exception as e:
        return {"error": f"Network/JSON error: {e}"}

    if "Note" in data:
        return {"error": "Alpha Vantage throttling / rate limit", "detail": data["Note"]}
    if "Error Message" in data:
        return {"error": "Alpha Vantage error", "detail": data["Error Message"]}

    q = data.get("Global Quote") or {}
    if not q:
        return {"error": "No data returned for symbol. Check symbol or try later."}

    return {
        "symbol": q.get("01. symbol", symbol),
        "open": q.get("02. open", "N/A"),
        "high": q.get("03. high", "N/A"),
        "low": q.get("04. low", "N/A"),
        "price": q.get("05. price", "N/A"),
        "volume": q.get("06. volume", "N/A"),
        "latest_day": q.get("07. latest trading day", "N/A"),
        "prev_close": q.get("08. previous close", "N/A"),
        "change": q.get("09. change", "N/A"),
        "change_percent": q.get("10. change percent", "N/A"),
    }

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.get("/quote")
def quote():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"ok": False, "error": "Missing symbol parameter (?symbol=SBIN.BSE)"}), 400
    data = fetch_live_quote(symbol)
    ok = "error" not in data
    status = 200 if ok else (429 if "limit" in (data.get("detail","").lower()) else 400)
    return jsonify({"ok": ok, **data}), status

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)
