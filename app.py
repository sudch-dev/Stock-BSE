import os
import time
import requests
from flask import Flask, jsonify, render_template, request

# ------------ Config ------------
# Keep your key secret: set ALPHA_VANTAGE_API_KEY in Render (or local) env
API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY") or os.environ.get("API_KEY")
ALPHA_URL = "https://www.alphavantage.co/query"
CACHE_TTL_SEC = 60  # short cache to ease free-tier rate limits

app = Flask(__name__)
_cache = {}  # ("daily", SYMBOL) -> (timestamp, data)

# ------------ Math helpers ------------
def _pct(a, b):
    if b in (0, None) or a is None:
        return 0.0
    return ((a - b) / b) * 100.0

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

def _linreg(y):
    n = len(y)
    if n < 2:
        return 0.0, (y[0] if y else 0.0), 0.0
    x = list(range(n))
    x_bar = _mean(x)
    y_bar = _mean(y)
    num = sum((xi - x_bar) * (yi - y_bar) for xi, yi in zip(x, y))
    den = sum((xi - x_bar) ** 2 for xi in x)
    slope = 0.0 if den == 0 else num / den
    intercept = y_bar - slope * x_bar
    ss_tot = sum((yi - y_bar) ** 2 for yi in y)
    ss_res = sum((yi - (intercept + slope * xi)) ** 2 for xi, yi in zip(x, y))
    r2 = 0.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot
    return slope, intercept, r2

def _rolling_high(arr, n):
    n = min(n, len(arr))
    return max(arr[:n]) if n > 0 else 0.0

def _rolling_low(arr, n):
    n = min(n, len(arr))
    return min(arr[:n]) if n > 0 else 0.0

# ------------ Swing points ------------
def _find_swings(highs, lows, lookback=2, max_points=10):
    """Find local highs/lows. Arrays are newest->older."""
    swings = []
    for i in range(lookback, len(highs) - lookback):
        is_high = True
        is_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j == i:
                continue
            if highs[i] < highs[j]:
                is_high = False
            if lows[i] > lows[j]:
                is_low = False
        if is_high:
            swings.append({"type": "H", "idx": i, "price": highs[i]})
        if is_low:
            swings.append({"type": "L", "idx": i, "price": lows[i]})
    swings.sort(key=lambda s: s["idx"])     # chronological
    swings = swings[-max_points:][::-1]     # most recent first
    return swings

# ------------ Pattern detectors ------------
def _detect_double_top(swings, tol_pct=1.5):
    hs = [s for s in swings if s["type"] == "H"]
    if len(hs) < 2:
        return None
    h1, h2 = hs[0], hs[1]
    near = abs(_pct(h1["price"], h2["price"])) <= tol_pct
    valley = next((s for s in swings if s["type"] == "L" and s["idx"] < h1["idx"] and s["idx"] > h2["idx"]), None)
    return {"name": "Double Top", "confidence": 0.7} if (near and valley) else None

def _detect_double_bottom(swings, tol_pct=1.5):
    ls = [s for s in swings if s["type"] == "L"]
    if len(ls) < 2:
        return None
    l1, l2 = ls[0], ls[1]
    near = abs(_pct(l1["price"], l2["price"])) <= tol_pct
    peak = next((s for s in swings if s["type"] == "H" and s["idx"] < l1["idx"] and s["idx"] > l2["idx"]), None)
    return {"name": "Double Bottom", "confidence": 0.7} if (near and peak) else None

def _detect_head_shoulders(swings, tol_pct=4.0):
    hs = [s for s in swings if s["type"] == "H"]
    ls = [s for s in swings if s["type"] == "L"]
    if len(hs) < 3 or len(ls) < 2:
        return None
    H1, H2, H3 = hs[0], hs[1], hs[2]
    middle_higher = H2["price"] > H1["price"] and H2["price"] > H3["price"]
    shoulders_near = abs(_pct(H1["price"], H3["price"])) <= tol_pct
    neck_near = abs(_pct(ls[0]["price"], ls[1]["price"])) <= tol_pct
    return {"name": "Head & Shoulders", "confidence": 0.65} if (middle_higher and shoulders_near and neck_near) else None

def _detect_triangles(swings, tol_pct=2.0):
    hs = [s for s in swings if s["type"] == "H"]
    ls = [s for s in swings if s["type"] == "L"]
    if len(hs) < 2 or len(ls) < 2:
        return None
    flat_highs = abs(_pct(hs[0]["price"], hs[1]["price"])) <= tol_pct
    flat_lows  = abs(_pct(ls[0]["price"], ls[1]["price"])) <= tol_pct
    rising_lows = ls[0]["price"] > ls[1]["price"]
    falling_highs = hs[0]["price"] < hs[1]["price"]
    if flat_highs and rising_lows:
        return {"name": "Ascending Triangle", "confidence": 0.6}
    if flat_lows and falling_highs:
        return {"name": "Descending Triangle", "confidence": 0.6}
    return None

def _detect_wedges(swings):
    hs = [s for s in swings if s["type"] == "H"]
    ls = [s for s in swings if s["type"] == "L"]
    if len(hs) < 3 or len(ls) < 3:
        return None
    highs_falling = hs[0]["price"] < hs[1]["price"] and hs[1]["price"] < hs[2]["price"]
    lows_rising   = ls[0]["price"] > ls[1]["price"] and ls[1]["price"] > ls[2]["price"]
    if highs_falling and lows_rising:
        return {"name": "Falling Wedge", "confidence": 0.55}
    highs_rising = hs[0]["price"] > hs[1]["price"] and hs[1]["price"] > hs[2]["price"]
    lows_falling = ls[0]["price"] < ls[1]["price"] and ls[1]["price"] < ls[2]["price"]
    if highs_rising and lows_falling:
        return {"name": "Rising Wedge", "confidence": 0.55}
    return None

def _detect_flags(closes, lookback_impulse=8, pullback_win=5):
    if len(closes) < lookback_impulse + pullback_win + 2:
        return None
    # arrays newest->older
    impulse_change = _pct(closes[lookback_impulse], closes[lookback_impulse + 1])
    last_change = _pct(closes[0], closes[pullback_win])
    range_recent = max(closes[:pullback_win]) - min(closes[:pullback_win])
    impulse_range = abs(closes[lookback_impulse + 1] - closes[lookback_impulse])
    tight = range_recent < (impulse_range * 0.6 if impulse_range != 0 else 1e-9)
    if impulse_change > 6 and last_change > -2 and tight:
        return {"name": "Bull Flag", "confidence": 0.55}
    if impulse_change < -6 and last_change < 2 and tight:
        return {"name": "Bear Flag", "confidence": 0.55}
    return None

def _detect_channels(closes, win=20):
    arr = list(reversed(closes[: min(win, len(closes))]))  # oldest->newest
    slope, intercept, r2 = _linreg(arr)
    if r2 < 0.6:
        return None
    if slope > 0:
        return {"name": "Channel Up", "confidence": 0.5}
    if slope < 0:
        return {"name": "Channel Down", "confidence": 0.5}
    return None

def _detect_rectangle(highs, lows, win=12):
    h = highs[:win]
    l = lows[:win]
    if not h or not l:
        return None
    top = max(h)
    bot = min(l)
    width_pct = abs(_pct(top, bot))
    return {"name": "Rectangle", "confidence": 0.45} if width_pct <= 10 else None

def detect_block_pattern(highs, lows, closes):
    swings = _find_swings(highs, lows, 2, 10)
    detectors = [
        _detect_head_shoulders,
        _detect_double_top,
        _detect_double_bottom,
        _detect_triangles,
        _detect_wedges,
        lambda *_: _detect_flags(closes),
        lambda *_: _detect_channels(closes),
        lambda *_: _detect_rectangle(highs, lows),
    ]
    for fn in detectors:
        res = fn(swings) if fn in (_detect_head_shoulders, _detect_double_top, _detect_double_bottom, _detect_triangles, _detect_wedges) else fn()
        if res:
            return res
    return {"name": "Sideways Block", "confidence": 0.4}

# ------------ Data fetch ------------
def fetch_daily(symbol):
    if not API_KEY:
        return {"error": "Missing ALPHA_VANTAGE_API_KEY in environment."}, None
    key = ("daily", symbol.upper())
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL_SEC:
        return None, _cache[key][1]

    params = {"function": "TIME_SERIES_DAILY", "symbol": symbol, "apikey": API_KEY}
    try:
        resp = requests.get(ALPHA_URL, params=params, timeout=30)
        data = resp.json()
    except Exception as e:
        return {"error": f"Network/JSON error: {e}"}, None

    if "Note" in data:
        return {"error": "API limit reached. Try again shortly."}, None
    series = data.get("Time Series (Daily)")
    if not series:
        return {"error": "Invalid response from Alpha Vantage or bad symbol."}, None

    dates = sorted(series.keys(), reverse=True)  # newest -> oldest
    highs = [float(series[d]["2. high"]) for d in dates]
    lows  = [float(series[d]["3. low"]) for d in dates]
    closes= [float(series[d]["4. close"]) for d in dates]

    payload = {
        "dates": dates,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "latest": {
            "date": dates[0],
            "open": float(series[dates[0]]["1. open"]),
            "high": float(series[dates[0]]["2. high"]),
            "low":  float(series[dates[0]]["3. low"]),
            "close":float(series[dates[0]]["4. close"]),
        },
        "prev_close": float(series[dates[1]]["4. close"]) if len(dates) > 1 else None,
    }
    _cache[key] = (now, payload)
    return None, payload

# ------------ Routes ------------
@app.route("/")
def index():
    return render_template("index.html")

@app.get("/api/analyze")
def api_analyze():
    symbol = request.args.get("symbol", "RELIANCE.BSE")
    err, data = fetch_daily(symbol)
    if err:
        return jsonify({"ok": False, "error": err["error"]}), 429 if "limit" in err.get("error","").lower() else 400

    latest = data["latest"]
    high = latest["high"]
    low = latest["low"]
    close = latest["close"]

    # Pivots
    pivot = (high + low + close) / 3.0
    r1 = 2 * pivot - low
    s1 = 2 * pivot - high

    # Stochastic %K (14) on newest->older arrays
    hh = _rolling_high(data["highs"], 14)
    ll = _rolling_low(data["lows"], 14)
    stochastic = ((close - ll) / (hh - ll) * 100.0) if (hh - ll) != 0 else 50.0
    if stochastic > 80:
        momentum = "Overbought – Possible Reversal or Breakout"
    elif stochastic < 20:
        momentum = "Oversold – Possible Bounce or Breakdown"
    else:
        momentum = "Neutral Momentum"

    pattern = detect_block_pattern(data["highs"], data["lows"], data["closes"])

    return jsonify({
        "ok": True,
        "symbol": symbol.upper(),
        "date": latest["date"],
        "open": latest["open"],
        "high": high,
        "low": low,
        "close": close,
        "prev_close": data["prev_close"],
        "pivot": round(pivot, 2),
        "r1": round(r1, 2),
        "s1": round(s1, 2),
        "stochastic_k": round(stochastic, 2),
        "momentum": momentum,
        "pattern": pattern,
        "closes": data["closes"],   # newest -> older
        "highs": data["highs"],
        "lows": data["lows"],
        "dates": data["dates"]
    })

if __name__ == "__main__":
    # Local dev: python app.py
    # Render: set Start Command to `gunicorn app:app`
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)