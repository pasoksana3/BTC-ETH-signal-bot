#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import statistics
import time
from collections import defaultdict
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

BASE = "https://contract.mexc.com"

TP1 = 0.005
TP2 = 0.007
DEFAULT_LOOKAHEAD = 12


def http_json(url, params=None, timeout=20):
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "MEXC-Backtest/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_klines(symbol, interval="Min5", days=30):
    now = int(time.time())
    start = now - days * 86400

    data = http_json(
        f"{BASE}/api/v1/contract/kline/{symbol}",
        {"interval": interval, "start": start, "end": now},
    )

    if not data.get("success"):
        raise RuntimeError(f"MEXC error: {data}")

    d = data["data"]
    rows = []

    n = min(
        len(d["time"]),
        len(d["open"]),
        len(d["close"]),
        len(d["high"]),
        len(d["low"]),
        len(d["vol"]),
    )

    for i in range(n):
        rows.append({
            "time": int(d["time"][i]),
            "open": float(d["open"][i]),
            "close": float(d["close"][i]),
            "high": float(d["high"][i]),
            "low": float(d["low"][i]),
            "vol": float(d["vol"][i]),
        })

    rows.sort(key=lambda x: x["time"])
    return rows


def sma(vals, p):
    if len(vals) < p:
        return None
    return sum(vals[-p:]) / p


def color(c):
    if c["close"] > c["open"]:
        return "G"
    if c["close"] < c["open"]:
        return "R"
    return "D"


def body(c):
    return abs(c["close"] - c["open"])


def atr(candles, p=14):
    if len(candles) < p + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i-1]
        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - prev["close"]),
            abs(c["low"] - prev["close"]),
        ))
    return sum(trs[-p:]) / p


def detect(candles, i):
    # Signal candle is candles[i].
    if i < 40:
        return None

    confirm = candles[i]
    exhaustion = candles[i-1]

    if color(confirm) not in ("G", "R"):
        return None

    ex_color = color(exhaustion)
    if ex_color not in ("G", "R") or color(confirm) == ex_color:
        return None

    run = 0
    j = i - 2
    while j >= 0 and color(candles[j]) == ex_color:
        run += 1
        j -= 1

    if run < 4 or run > 6:
        return None

    series = candles[i-1-run:i-1]
    avg_body = sum(body(x) for x in series) / len(series)

    if body(exhaustion) > avg_body * 1.35:
        return None

    if ex_color == "R":
        if not (confirm["close"] > exhaustion["high"]):
            return None
        direction = "LONG"
    else:
        if not (confirm["close"] < exhaustion["low"]):
            return None
        direction = "SHORT"

    closes = [x["close"] for x in candles[:i+1]]
    ma5, ma14, ma30 = sma(closes,5), sma(closes,14), sma(closes,30)
    a = atr(candles[:i+1])

    if None in (ma5, ma14, ma30) or not a:
        return None

    score = 25
    if body(exhaustion) <= max(body(series[-1]) * .90, avg_body * .90):
        score += 15
    else:
        score += 5

    score += 25

    if (direction == "LONG" and confirm["close"] > ma5) or \
       (direction == "SHORT" and confirm["close"] < ma5):
        score += 10

    if (direction == "LONG" and confirm["close"] > ma14) or \
       (direction == "SHORT" and confirm["close"] < ma14):
        score += 5

    if (direction == "LONG" and confirm["close"] > ma30) or \
       (direction == "SHORT" and confirm["close"] < ma30):
        score += 5

    if body(confirm) / max(body(exhaustion), 1e-12) >= 1:
        score += 10
    elif body(confirm) / max(body(exhaustion), 1e-12) >= .7:
        score += 5

    if body(confirm) / max(confirm["high"]-confirm["low"], 1e-12) >= .6:
        score += 5

    return {
        "i": i,
        "direction": direction,
        "entry": confirm["close"],
        "score": min(score,100),
        "run": run,
    }


def evaluate(candles, sig, lookahead):
    i = sig["i"]
    entry = sig["entry"]

    if sig["direction"] == "LONG":
        tp1 = entry * (1 + TP1)
        tp2 = entry * (1 + TP2)
        invalid = candles[i-1]["low"]

        for c in candles[i+1:i+1+lookahead]:
            # Conservative: if both TP and invalidation are touched in one
            # candle, count invalidation first.
            if c["low"] <= invalid:
                return "INVALIDATION"
            if c["high"] >= tp2:
                return "TP2"
            if c["high"] >= tp1:
                return "TP1"

    else:
        tp1 = entry * (1 - TP1)
        tp2 = entry * (1 - TP2)
        invalid = candles[i-1]["high"]

        for c in candles[i+1:i+1+lookahead]:
            if c["high"] >= invalid:
                return "INVALIDATION"
            if c["low"] <= tp2:
                return "TP2"
            if c["low"] <= tp1:
                return "TP1"

    return "UNRESOLVED"


def run(symbol, days, lookahead, min_score):
    candles = get_klines(symbol, days=days)
    signals = []

    for i in range(40, len(candles)):
        s = detect(candles, i)
        if s and s["score"] >= min_score:
            s["result"] = evaluate(candles, s, lookahead)
            signals.append(s)

    return signals


def print_report(symbol, signals):
    total = len(signals)
    counts = defaultdict(int)

    for s in signals:
        counts[s["result"]] += 1

    print("\n" + "="*60)
    print(symbol)
    print("="*60)
    print(f"Signals:      {total}")
    print(f"TP1:          {counts['TP1']}")
    print(f"TP2:          {counts['TP2']}")
    print(f"Invalidation: {counts['INVALIDATION']}")
    print(f"Unresolved:   {counts['UNRESOLVED']}")

    resolved = total - counts["UNRESOLVED"]

    if resolved:
        print(f"TP1-or-better among resolved: "
              f"{(counts['TP1']+counts['TP2'])/resolved*100:.2f}%")
        print(f"TP2 among resolved: "
              f"{counts['TP2']/resolved*100:.2f}%")

    by_run = defaultdict(list)
    by_score = defaultdict(list)

    for s in signals:
        by_run[s["run"]].append(s)
        bucket = (s["score"] // 5) * 5
        by_score[bucket].append(s)

    print("\nBy run length:")
    for run in sorted(by_run):
        arr = by_run[run]
        r = [x["result"] for x in arr]
        res = len(r) - r.count("UNRESOLVED")
        hit = sum(x in ("TP1","TP2") for x in r)
        rate = hit/res*100 if res else 0
        print(f"  {run} candles: n={len(r):4d}, resolved={res:4d}, "
              f"TP1+={rate:6.2f}%")

    print("\nBy score:")
    for bucket in sorted(by_score):
        arr = by_score[bucket]
        r = [x["result"] for x in arr]
        res = len(r) - r.count("UNRESOLVED")
        hit = sum(x in ("TP1","TP2") for x in r)
        rate = hit/res*100 if res else 0
        print(f"  {bucket:3d}-{bucket+4:3d}: n={len(r):4d}, "
              f"resolved={res:4d}, TP1+={rate:6.2f}%")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", help="Example BTC_USDT")
    p.add_argument("--symbols", help="Comma-separated symbols")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--lookahead", type=int, default=DEFAULT_LOOKAHEAD)
    p.add_argument("--min-score", type=int, default=85)
    args = p.parse_args()

    symbols = []
    if args.symbol:
        symbols.append(args.symbol)
    if args.symbols:
        symbols.extend(x.strip() for x in args.symbols.split(",") if x.strip())

    if not symbols:
        p.error("Use --symbol or --symbols")

    for symbol in symbols:
        try:
            signals = run(symbol, args.days, args.lookahead, args.min_score)
            print_report(symbol, signals)
        except Exception as e:
            print(f"{symbol}: ERROR: {e}")


if __name__ == "__main__":
    main()
