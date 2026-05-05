import subprocess
import sys
import os
import json
import time
import requests
from flask import Flask, render_template, redirect, url_for, jsonify, request, Response

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import storage

app = Flask(__name__)
CG_BASE = "https://api.coingecko.com/api/v3"

def _local_usage(api_key: str):
    """CoinGecko /key is PRO-only. Returns locally-tracked (used, left) from DB."""
    for a in storage.get_all_accounts():
        if a["api_key"] == api_key:
            return a["calls_used"], a["calls_left"]
    return 0, 10000


@app.route("/")
def index():
    accounts = storage.get_all_accounts()
    active = storage.get_active_account()
    active_id = active["id"] if active else None
    active_pinned = bool(active.get("is_pinned")) if active else False
    return render_template("index.html", accounts=accounts, active_id=active_id, active_pinned=active_pinned)


@app.route("/refresh/<api_key>", methods=["POST"])
def refresh(api_key):
    return redirect(url_for("index"))


@app.route("/refresh-all", methods=["POST"])
def refresh_all():
    return redirect(url_for("index"))


@app.route("/create", methods=["POST"])
def create():
    main_script = os.path.join(os.path.dirname(__file__), "..", "main.py")
    subprocess.Popen([sys.executable, main_script, "--count", "1"])
    return redirect(url_for("index"))


@app.route("/pin/<int:account_id>", methods=["POST"])
def pin(account_id):
    storage.pin_account(account_id)
    return redirect(url_for("index"))


@app.route("/unpin", methods=["POST"])
def unpin():
    storage.unpin_all()
    return redirect(url_for("index"))


@app.route("/delete/<int:account_id>", methods=["POST"])
def delete(account_id):
    storage.delete_account(account_id)
    return redirect(url_for("index"))


@app.route("/api/prices")
def api_prices():
    account = storage.get_active_account()
    if not account:
        return jsonify({"error": "No active API key available"}), 503

    coins = ["bitcoin", "ethereum", "solana", "binancecoin", "ripple", "cardano"]
    try:
        r = requests.get(
            f"{CG_BASE}/simple/price",
            params={
                "ids": ",".join(coins),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_market_cap": "true",
            },
            headers={"x-cg-demo-api-key": account["api_key"]},
            timeout=8,
        )
        if r.status_code == 429:
            return jsonify({"error": "rate_limited"}), 429
        if r.ok:
            storage.increment_calls_used(account["api_key"])
            return jsonify({
                "prices": r.json(),
                "active_key": account["api_key"][:12] + "••••",
                "active_id": account["id"],
            })
        return jsonify({"error": f"CoinGecko returned {r.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/global")
def api_global():
    account = storage.get_active_account()
    if not account:
        return jsonify({"error": "No active API key"}), 503
    try:
        r = requests.get(
            f"{CG_BASE}/global",
            headers={"x-cg-demo-api-key": account["api_key"]},
            timeout=8,
        )
        if r.status_code == 429:
            return jsonify({"error": "rate_limited"}), 429
        if r.ok:
            storage.increment_calls_used(account["api_key"])
            return jsonify(r.json().get("data", {}))
        return jsonify({"error": f"CoinGecko returned {r.status_code}"}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trending")
def api_trending():
    account = storage.get_active_account()
    if not account:
        return jsonify({"error": "No active API key"}), 503
    try:
        r = requests.get(
            f"{CG_BASE}/search/trending",
            headers={"x-cg-demo-api-key": account["api_key"]},
            timeout=8,
        )
        if r.status_code == 429:
            return jsonify({"error": "rate_limited"}), 429
        if r.ok:
            storage.increment_calls_used(account["api_key"])
            return jsonify(r.json())
        return jsonify({"error": f"CoinGecko returned {r.status_code}"}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/markets")
def api_markets():
    account = storage.get_active_account()
    if not account:
        return jsonify({"error": "No active API key"}), 503
    try:
        r = requests.get(
            f"{CG_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 10,
                "page": 1,
                "sparkline": "true",
                "price_change_percentage": "24h,7d",
            },
            headers={"x-cg-demo-api-key": account["api_key"]},
            timeout=10,
        )
        if r.status_code == 429:
            return jsonify({"error": "rate_limited"}), 429
        if r.ok:
            storage.increment_calls_used(account["api_key"])
            return jsonify(r.json())
        return jsonify({"error": f"CoinGecko returned {r.status_code}"}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug-key")
def api_debug_key():
    """Shows local DB usage (CoinGecko /key is PRO-only for Demo keys)."""
    account = storage.get_active_account()
    if not account:
        return jsonify({"error": "No active account"}), 503
    return jsonify({
        "note": "CoinGecko /key is PRO-only — usage is tracked locally",
        "api_key": account["api_key"][:12] + "••••",
        "calls_used": account["calls_used"],
        "calls_left": account["calls_left"],
    })


@app.route("/api/usage-stats")
def api_usage_stats():
    """Return current DB usage totals — polled by the frontend to refresh stats cards."""
    accounts = storage.get_all_accounts()
    active = storage.get_active_account()
    return jsonify({
        "total_keys": len(accounts),
        "total_used": sum(a["calls_used"] for a in accounts),
        "total_left": sum(a["calls_left"] for a in accounts),
        "active_id": active["id"] if active else None,
        "active_pinned": bool(active.get("is_pinned")) if active else False,
        "keys": [
            {"id": a["id"], "calls_used": a["calls_used"], "calls_left": a["calls_left"]}
            for a in accounts
        ],
    })


def _resample_daily(prices_data):
    """Collapse hourly/sub-hourly data to one price per UTC calendar day (last tick)."""
    seen = {}
    for ts_ms, price in prices_data:
        day = ts_ms // 86_400_000
        seen[day] = (ts_ms, price)
    return [seen[d] for d in sorted(seen)]


def compute_backtest(prices_data, strategy, initial_capital=10000.0):
    daily = _resample_daily(prices_data)
    prices = [p[1] for p in daily]
    timestamps = [p[0] for p in daily]
    n = len(prices)
    benchmark_return = round((prices[-1] / prices[0] - 1) * 100, 2)

    if strategy == "hold":
        shares = initial_capital / prices[0]
        final_value = shares * prices[-1]
        return {
            "strategy_name": "Buy & Hold",
            "trades": 1,
            "initial": round(initial_capital, 2),
            "final": round(final_value, 2),
            "return_pct": round((final_value / initial_capital - 1) * 100, 2),
            "benchmark_return": benchmark_return,
            "prices": daily,
            "trade_signals": [
                {"type": "buy",  "price": prices[0],  "ts": timestamps[0]},
                {"type": "sell", "price": prices[-1], "ts": timestamps[-1]},
            ],
        }

    if strategy == "sma":
        short_w, long_w = 7, 21

        def _sma(data, w):
            return [sum(data[i - w:i]) / w if i >= w else None for i in range(n)]

        short_ma = _sma(prices, short_w)
        long_ma  = _sma(prices, long_w)
        cash, shares_held, position = float(initial_capital), 0.0, 0
        trades = []
        for i in range(1, n):
            s, l, sp, lp = short_ma[i], long_ma[i], short_ma[i - 1], long_ma[i - 1]
            if None in (s, l, sp, lp):
                continue
            if s > l and sp <= lp and position == 0:
                shares_held = cash / prices[i]; cash = 0.0; position = 1
                trades.append({"type": "buy",  "price": prices[i], "ts": timestamps[i]})
            elif s < l and sp >= lp and position == 1:
                cash = shares_held * prices[i]; shares_held = 0.0; position = 0
                trades.append({"type": "sell", "price": prices[i], "ts": timestamps[i]})
        final_value = cash + shares_held * prices[-1]
        return {
            "strategy_name": f"SMA Crossover ({short_w}d / {long_w}d)",
            "trades": len(trades),
            "initial": round(initial_capital, 2),
            "final": round(final_value, 2),
            "return_pct": round((final_value / initial_capital - 1) * 100, 2),
            "benchmark_return": benchmark_return,
            "prices": daily,
            "sma_short": [[timestamps[i], round(short_ma[i], 6)] for i in range(n) if short_ma[i] is not None],
            "sma_long":  [[timestamps[i], round(long_ma[i],  6)] for i in range(n) if long_ma[i]  is not None],
            "trade_signals": trades,
        }

    if strategy == "rsi":
        period = 14

        def _rsi(data, p):
            if len(data) <= p:
                return [None] * len(data)
            vals = [None] * p
            chgs = [data[i] - data[i - 1] for i in range(1, p + 1)]
            avg_g = sum(max(c, 0) for c in chgs) / p
            avg_l = sum(max(-c, 0) for c in chgs) / p
            vals.append(100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l))
            for i in range(p + 1, len(data)):
                chg = data[i] - data[i - 1]
                avg_g = (avg_g * (p - 1) + max(chg, 0))  / p
                avg_l = (avg_l * (p - 1) + max(-chg, 0)) / p
                vals.append(100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l))
            return vals

        rsi = _rsi(prices, period)
        cash, shares_held, position = float(initial_capital), 0.0, 0
        trades = []
        for i in range(n):
            if rsi[i] is None:
                continue
            if rsi[i] < 30 and position == 0:
                shares_held = cash / prices[i]; cash = 0.0; position = 1
                trades.append({"type": "buy",  "price": prices[i], "ts": timestamps[i]})
            elif rsi[i] > 70 and position == 1:
                cash = shares_held * prices[i]; shares_held = 0.0; position = 0
                trades.append({"type": "sell", "price": prices[i], "ts": timestamps[i]})
        final_value = cash + shares_held * prices[-1]
        return {
            "strategy_name": f"RSI ({period}) — buy <30 / sell >70",
            "trades": len(trades),
            "initial": round(initial_capital, 2),
            "final": round(final_value, 2),
            "return_pct": round((final_value / initial_capital - 1) * 100, 2),
            "benchmark_return": benchmark_return,
            "prices": daily,
            "rsi": [[timestamps[i], round(rsi[i], 2)] for i in range(n) if rsi[i] is not None],
            "trade_signals": trades,
        }

    return {"error": "Unknown strategy"}


@app.route("/search")
def search_page():
    return render_template("search.html")


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400
    account = storage.get_active_account()
    if not account:
        return jsonify({"error": "No active API key"}), 503
    try:
        r = requests.get(
            f"{CG_BASE}/search",
            params={"query": query},
            headers={"x-cg-demo-api-key": account["api_key"]},
            timeout=8,
        )
        if r.status_code == 429:
            return jsonify({"error": "rate_limited"}), 429
        if r.ok:
            return jsonify(r.json())
        return jsonify({"error": f"CoinGecko returned {r.status_code}"}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/backtest")
def backtest_page():
    return render_template("backtest.html")


@app.route("/api/backtest")
def api_backtest():
    coin_id  = request.args.get("coin",     "bitcoin").strip()
    days     = request.args.get("days",     "90")
    strategy = request.args.get("strategy", "hold")
    try:
        capital = float(request.args.get("capital", "10000"))
    except ValueError:
        capital = 10000.0
    account = storage.get_active_account()
    if not account:
        return jsonify({"error": "No active API key"}), 503
    try:
        r = requests.get(
            f"{CG_BASE}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": days},
            headers={"x-cg-demo-api-key": account["api_key"]},
            timeout=15,
        )
        if r.status_code == 429:
            return jsonify({"error": "rate_limited"}), 429
        if r.status_code == 404:
            return jsonify({"error": f"Coin '{coin_id}' not found — use the Search page to find the exact ID"}), 404
        if not r.ok:
            return jsonify({"error": f"CoinGecko returned {r.status_code}"}), r.status_code
        prices_data = r.json().get("prices", [])
        if len(prices_data) < 22:
            return jsonify({"error": "Not enough data (need at least 22 data points); try a longer period"}), 400
        result = compute_backtest(prices_data, strategy, capital)
        if "error" in result:
            return jsonify(result), 400
        result["coin_id"] = coin_id
        result["days"] = int(days)
        storage.increment_calls_used(account["api_key"])
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


_STRESS_ENDPOINTS = [
    (f"{CG_BASE}/simple/price",    {"ids": "bitcoin",          "vs_currencies": "usd"}),
    (f"{CG_BASE}/global",          {}),
    (f"{CG_BASE}/simple/price",    {"ids": "ethereum,solana",  "vs_currencies": "usd"}),
    (f"{CG_BASE}/search/trending", {}),
    (f"{CG_BASE}/coins/markets",   {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 5}),
    (f"{CG_BASE}/simple/price",    {"ids": "binancecoin,ripple,cardano", "vs_currencies": "usd"}),
]

_STRESS_ENDPOINT_NAMES = [
    "simple/price — BTC",
    "global",
    "simple/price — ETH, SOL",
    "search/trending",
    "coins/markets — top 5",
    "simple/price — BNB, XRP, ADA",
]


@app.route("/api/stress-test")
def api_stress_test():
    """SSE endpoint — streams live progress as it burns through N API calls."""
    try:
        n = max(1, min(int(request.args.get("n", "20")), 500))
    except ValueError:
        n = 20

    account = storage.get_active_account()
    if not account:
        def _err():
            yield f"data: {json.dumps({'error': 'No active API key'})}\n\n"
        return Response(_err(), mimetype="text/event-stream")

    api_key = account["api_key"]

    def generate():
        calls_made = 0
        errors = 0
        rate_limited = 0

        acct_snap = storage.get_active_account() or {}
        baseline_used = acct_snap.get("calls_used", 0)
        baseline_left = acct_snap.get("calls_left", 10000)
        local_delta = 0  # successful calls this run

        for i in range(n):
            idx = i % len(_STRESS_ENDPOINTS)
            url, params = _STRESS_ENDPOINTS[idx]
            ep_name = _STRESS_ENDPOINT_NAMES[idx]
            status = 0
            latency_ms = 0
            try:
                t0 = time.time()
                r = requests.get(
                    url, params=params,
                    headers={"x-cg-demo-api-key": api_key},
                    timeout=10,
                )
                latency_ms = round((time.time() - t0) * 1000)
                status = r.status_code
                if status == 429:
                    rate_limited += 1
                    yield f"data: {json.dumps({'done': calls_made, 'total': n, 'status': 429, 'errors': errors, 'rate_limited': rate_limited, 'message': 'Rate limited — pausing 15 s', 'calls_used': baseline_used + local_delta, 'calls_left': max(0, baseline_left - local_delta), 'endpoint': ep_name, 'latency_ms': latency_ms})}\n\n"
                    time.sleep(15)
                    continue
                elif r.ok:
                    calls_made += 1
                    local_delta += 1
                    storage.increment_calls_used(api_key)
                else:
                    errors += 1
                    calls_made += 1
            except Exception as exc:
                latency_ms = round((time.time() - t0) * 1000) if t0 else 0
                errors += 1
                calls_made += 1

            yield f"data: {json.dumps({'done': calls_made, 'total': n, 'status': status, 'errors': errors, 'rate_limited': rate_limited, 'calls_used': baseline_used + local_delta, 'calls_left': max(0, baseline_left - local_delta), 'endpoint': ep_name, 'latency_ms': latency_ms})}\n\n"
            time.sleep(0.25)

        yield f"data: {json.dumps({'done': calls_made, 'total': n, 'complete': True, 'errors': errors, 'rate_limited': rate_limited, 'calls_used': baseline_used + local_delta, 'calls_left': max(0, baseline_left - local_delta)})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    print("Dashboard running at http://localhost:5000")
    app.run(debug=False, port=5000, threaded=True)
