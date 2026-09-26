#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MEXC Futures -> Telegram signal bot
-----------------------------------
Один файл. Бот:
1) отримує USDT-M perpetual контракти MEXC;
2) сканує 5m свічки;
3) шукає серію 4-6 однакових свічок + виснаження + свічку підтвердження;
4) будує 10m з 5m локально;
5) для кандидата перевіряє 15m та 1h;
6) розраховує TP1 = 0.50%, TP2 = 0.70%;
7) надсилає сигнал у Telegram;
8) не відкриває угоди автоматично.

ВАЖЛИВО:
- SCORE 85+ — це внутрішній технічний score, НЕ статистична ймовірність 85%.
- Реальні 85-100% win-rate можна заявляти лише після окремого backtest.
"""

import json
import math
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ============================================================
#                 НАЛАШТУВАННЯ — ЗАПОВНИ ЦЕ
# ============================================================

TELEGRAM_BOT_TOKEN = "ВСТАВ_СЮДИ_ТОКЕН_ВІД_BOTFATHER"
TELEGRAM_CHAT_ID = "-5417788354"

# Як часто запускати повний цикл.
# Для всіх монет рекомендовано 30-60 сек.
SCAN_SECONDS = 60

# 0 = усі USDT perpetual контракти.
# Наприклад 100 = тільки 100 найбільш активних.
MAX_SYMBOLS = 0

# Мінімальний внутрішній score сигналу.
MIN_SCORE = 85

# Скільки паралельних worker'ів використовувати.
# Залишено помірним, щоб не створювати зайве навантаження на API.
MAX_WORKERS = 6

# Мінімальний обсяг тіла останньої свічки відносно ціни.
# 0.0002 = 0.02%.
MIN_BODY_PCT = 0.0002

# Не надсилати повторний сигнал для того самого symbol/tf/direction
# протягом цього часу.
COOLDOWN_MINUTES = 30

# Якщо True — після запуску надсилається тестове повідомлення.
SEND_STARTUP_MESSAGE = True

MEXC_BASE = "https://contract.mexc.com"
TELEGRAM_BASE = "https://api.telegram.org/bot"

# Інтервали MEXC, які використовуємо напряму.
TF_5M = "Min5"
TF_15M = "Min15"
TF_1H = "Min60"


# ============================================================
#                 ГЛОБАЛЬНИЙ СТАН
# ============================================================

last_sent = {}          # (symbol, tf, direction) -> candle_timestamp
last_sent_time = {}    # (symbol, tf, direction) -> unix time
request_lock = threading.Lock()
last_request_time = 0.0

# Діагностика: показуємо, на якому етапі відсіюються монети.
DIAGNOSTICS_ENABLED = True
DIAG_ORDER = [
    "NO_5M_DATA",
    "BASE_PATTERN",
    "BASE_SCORE",
    "10M",
    "15M_1H_DATA",
    "MIN_SCORE",
    "SIGNAL",
]


# ============================================================
#                 HTTP
# ============================================================

def http_json(url, params=None, timeout=15):
    global last_request_time

    if params:
        url = url + "?" + urlencode(params)

    # Простий глобальний rate limiter.
    # ~8-9 запитів/сек максимум.
    with request_lock:
        now = time.time()
        wait = 0.12 - (now - last_request_time)
        if wait > 0:
            time.sleep(wait)
        last_request_time = time.time()

    req = Request(
        url,
        headers={
            "User-Agent": "MEXC-Signal-Bot/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"[HTTP ERROR] {url} -> {e}")
        return None


# ============================================================
#                 TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or "ВСТАВ" in TELEGRAM_BOT_TOKEN:
        print("[TELEGRAM] Не заданий TELEGRAM_BOT_TOKEN")
        return False

    if not TELEGRAM_CHAT_ID or "ВСТАВ" in str(TELEGRAM_CHAT_ID):
        print("[TELEGRAM] Не заданий TELEGRAM_CHAT_ID")
        return False

    url = TELEGRAM_BASE + TELEGRAM_BOT_TOKEN + "/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "MEXC-Signal-Bot/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
            if result.get("ok"):
                return True
            print("[TELEGRAM ERROR]", result)
            return False
    except Exception as e:
        print("[TELEGRAM ERROR]", e)
        return False


# ============================================================
#                 MEXC
# ============================================================

def get_contracts():
    data = http_json(MEXC_BASE + "/api/v1/contract/detail")
    if not data or not data.get("success"):
        return []

    result = data.get("data", [])
    symbols = []

    for x in result:
        symbol = x.get("symbol")
        quote = str(x.get("quoteCoin", "")).upper()
        state = x.get("state")

        # Беремо USDT perpetual контракти.
        if not symbol:
            continue

        if quote != "USDT":
            continue

        # state=0 зазвичай означає активний контракт.
        if state not in (None, 0, "0"):
            continue

        symbols.append(symbol)

    return sorted(set(symbols))


def get_tickers():
    """
    Необов'язкова функція для сортування за активністю.
    Якщо endpoint не поверне список — бот просто працюватиме без фільтра.
    """
    data = http_json(MEXC_BASE + "/api/v1/contract/ticker")
    if not data or not data.get("success"):
        return {}

    result = data.get("data")

    if not isinstance(result, list):
        return {}

    out = {}
    for x in result:
        symbol = x.get("symbol")
        if not symbol:
            continue

        volume = (
            x.get("volume")
            or x.get("holdVol")
            or x.get("amount")
            or 0
        )

        try:
            volume = float(volume)
        except Exception:
            volume = 0.0

        out[symbol] = volume

    return out


def get_klines(symbol, interval, limit=120):
    """
    MEXC contract kline:
    GET /api/v1/contract/kline/{symbol}

    Повертаємо список словників:
    time/open/high/low/close/vol
    """
    now = int(time.time())
    seconds = {
        "Min5": 300,
        "Min15": 900,
        "Min60": 3600,
    }.get(interval, 300)

    start = now - seconds * (limit + 5)

    url = MEXC_BASE + f"/api/v1/contract/kline/{symbol}"
    data = http_json(
        url,
        params={
            "interval": interval,
            "start": start,
            "end": now,
        },
        timeout=15,
    )

    if not data or not data.get("success"):
        return []

    raw = data.get("data")
    if not isinstance(raw, dict):
        return []

    try:
        times = raw.get("time", [])
        opens = raw.get("open", [])
        closes = raw.get("close", [])
        highs = raw.get("high", [])
        lows = raw.get("low", [])
        vols = raw.get("vol", [])

        n = min(
            len(times),
            len(opens),
            len(closes),
            len(highs),
            len(lows),
            len(vols),
        )

        candles = []

        for i in range(n):
            candles.append({
                "time": int(times[i]),
                "open": float(opens[i]),
                "close": float(closes[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "vol": float(vols[i]),
            })

        candles.sort(key=lambda x: x["time"])

        # Відкидаємо поточну незакриту свічку.
        if candles:
            last = candles[-1]["time"]
            if now < last + seconds:
                candles = candles[:-1]

        return candles[-limit:]

    except Exception as e:
        print(f"[KLINE ERROR] {symbol} {interval}: {e}")
        return []


# ============================================================
#                 МАТЕМАТИКА / ІНДИКАТОРИ
# ============================================================

def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def candle_color(c):
    if c["close"] > c["open"]:
        return "GREEN"
    if c["close"] < c["open"]:
        return "RED"
    return "DOJI"


def body(c):
    return abs(c["close"] - c["open"])


def range_size(c):
    return max(c["high"] - c["low"], 1e-12)


def body_pct(c):
    mid = max(abs(c["close"]), 1e-12)
    return body(c) / mid


def upper_wick(c):
    return c["high"] - max(c["open"], c["close"])


def lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]

        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"]),
        )
        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


def aggregate_10m_from_5m(candles):
    """
    Створює 10m із 5m локально.
    MEXC docs не використовує Min10, тому це робимо всередині бота.
    """
    buckets = {}

    for c in candles:
        bucket = (c["time"] // 600) * 600

        if bucket not in buckets:
            buckets[bucket] = {
                "time": bucket,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "vol": c["vol"],
                "_count": 1,
            }
        else:
            b = buckets[bucket]
            b["high"] = max(b["high"], c["high"])
            b["low"] = min(b["low"], c["low"])
            b["close"] = c["close"]
            b["vol"] += c["vol"]
            b["_count"] += 1

    out = []
    now = int(time.time())

    for k in sorted(buckets):
        b = buckets[k]

        # Беремо лише повні 10m свічки.
        if b["_count"] >= 2 and now >= b["time"] + 600:
            b = dict(b)
            b.pop("_count", None)
            out.append(b)

    return out


# ============================================================
#                 PATTERN ENGINE
# ============================================================

def find_base_pattern(candles, diagnostics=None):
    """
    Та сама торгова логіка, але повертаємо причину відсіву.
    """
    if len(candles) < 60:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    confirm = candles[-1]
    exhaustion = candles[-2]

    if body_pct(confirm) < MIN_BODY_PCT:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    confirm_color = candle_color(confirm)
    exhaustion_color = candle_color(exhaustion)

    if confirm_color not in ("GREEN", "RED"):
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    run_color = exhaustion_color
    run_len = 0

    for c in reversed(candles[:-2]):
        if candle_color(c) == run_color:
            run_len += 1
        else:
            break

    if run_len < 4 or run_len > 6:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    if confirm_color == exhaustion_color:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    series = candles[-2 - run_len:-2]
    if len(series) < 4:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    bodies = [body(x) for x in series]
    avg_body = sum(bodies) / max(len(bodies), 1)
    exhaustion_ratio = body(exhaustion) / max(avg_body, 1e-12)

    if exhaustion_ratio > 1.35:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    shrinking = body(exhaustion) <= max(
        body(series[-1]) * 0.90,
        avg_body * 0.90,
    )

    bullish_confirmation = (
        exhaustion_color == "RED"
        and confirm_color == "GREEN"
        and confirm["close"] > exhaustion["high"]
    )

    bearish_confirmation = (
        exhaustion_color == "GREEN"
        and confirm_color == "RED"
        and confirm["close"] < exhaustion["low"]
    )

    if not bullish_confirmation and not bearish_confirmation:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    closes = [x["close"] for x in candles]
    ma5 = sma(closes, 5)
    ma14 = sma(closes, 14)
    ma30 = sma(closes, 30)

    if ma5 is None or ma14 is None or ma30 is None:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    atr14 = atr(candles, 14)
    if atr14 is None or atr14 <= 0:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    atr_pct = atr14 / max(confirm["close"], 1e-12)

    if atr_pct < 0.0008:
        if diagnostics is not None:
            diagnostics["BASE_PATTERN"] += 1
        return None

    direction = "LONG" if bullish_confirmation else "SHORT"

    score = 0
    reasons = []

    score += 25
    reasons.append(f"{run_len} однакових свічок")

    if shrinking:
        score += 15
        reasons.append("тіло зменшилось")
    else:
        score += 5

    score += 25
    reasons.append("є пробій exhaustion")

    if direction == "LONG":
        if confirm["close"] > ma5:
            score += 10
            reasons.append("ціна > MA5")
    else:
        if confirm["close"] < ma5:
            score += 10
            reasons.append("ціна < MA5")

    if direction == "LONG":
        if confirm["close"] > ma14:
            score += 5
            reasons.append("ціна > MA14")
    else:
        if confirm["close"] < ma14:
            score += 5
            reasons.append("ціна < MA14")

    if direction == "LONG":
        if confirm["close"] > ma30:
            score += 5
            reasons.append("ціна > MA30")
    else:
        if confirm["close"] < ma30:
            score += 5
            reasons.append("ціна < MA30")

    confirm_body_ratio = body(confirm) / max(body(exhaustion), 1e-12)

    if confirm_body_ratio >= 1.0:
        score += 10
        reasons.append("сильна confirmation-свічка")
    elif confirm_body_ratio >= 0.70:
        score += 5

    body_range_ratio = body(confirm) / max(range_size(confirm), 1e-12)
    if body_range_ratio >= 0.60:
        score += 5
        reasons.append("сильне тіло")

    return {
        "direction": direction,
        "score": min(score, 100),
        "run_len": run_len,
        "confirm_time": confirm["time"],
        "entry": confirm["close"],
        "ma5": ma5,
        "ma14": ma14,
        "ma30": ma30,
        "atr_pct": atr_pct,
        "reasons": reasons,
        "series": series,
        "exhaustion": exhaustion,
        "confirmation": confirm,
    }


# ============================================================
#                 HIGHER TF FILTER
# ============================================================

def higher_tf_score(symbol, base_signal):
    """
    Для кандидата дивимося 15m + 1h.
    Не вимагаємо повної однаковості тренду, бо патерн шукає
    короткий рух 0.5-0.7%, у тому числі від локального розвороту.
    """

    k15 = get_klines(symbol, TF_15M, 80)
    k1h = get_klines(symbol, TF_1H, 80)

    if len(k15) < 35 or len(k1h) < 35:
        return None

    def tf_state(k):
        closes = [x["close"] for x in k]
        last = closes[-1]
        ma5 = sma(closes, 5)
        ma14 = sma(closes, 14)
        ma30 = sma(closes, 30)

        if None in (ma5, ma14, ma30):
            return 0, "N/A"

        if base_signal["direction"] == "LONG":
            points = 0
            if last > ma5:
                points += 1
            if ma5 > ma14:
                points += 1
            if last > ma30:
                points += 1

            if points == 3:
                return 3, "bull"
            if points == 2:
                return 2, "bull/neutral"
            if points == 1:
                return 1, "neutral"
            return 0, "bear"

        else:
            points = 0
            if last < ma5:
                points += 1
            if ma5 < ma14:
                points += 1
            if last < ma30:
                points += 1

            if points == 3:
                return 3, "bear"
            if points == 2:
                return 2, "bear/neutral"
            if points == 1:
                return 1, "neutral"
            return 0, "bull"

    s15, state15 = tf_state(k15)
    s1h, state1h = tf_state(k1h)

    bonus = 0

    if s15 >= 2:
        bonus += 8

    if s1h >= 2:
        bonus += 7

    return {
        "score_bonus": bonus,
        "score15": s15,
        "score1h": s1h,
        "state15": state15,
        "state1h": state1h,
    }


# ============================================================
#                 SIGNAL
# ============================================================

def make_signal(symbol, diagnostics=None):
    k5 = get_klines(symbol, TF_5M, 120)

    if len(k5) < 70:
        if diagnostics is not None:
            diagnostics["NO_5M_DATA"] += 1
        return None

    signal = find_base_pattern(k5, diagnostics)

    if not signal:
        return None

    if signal["score"] < 65:
        if diagnostics is not None:
            diagnostics["BASE_SCORE"] += 1
        return None

    k10 = aggregate_10m_from_5m(k5)

    if len(k10) >= 30:
        closes10 = [x["close"] for x in k10]
        ma5_10 = sma(closes10, 5)

        if ma5_10 is not None:
            if signal["direction"] == "LONG" and k10[-1]["close"] > ma5_10:
                signal["score"] += 5
                signal["reasons"].append("10m > MA5")
            elif signal["direction"] == "SHORT" and k10[-1]["close"] < ma5_10:
                signal["score"] += 5
                signal["reasons"].append("10m < MA5")

    ht = higher_tf_score(symbol, signal)

    if not ht:
        if diagnostics is not None:
            diagnostics["15M_1H_DATA"] += 1
        return None

    signal["score"] = min(signal["score"] + ht["score_bonus"], 100)
    signal["higher"] = ht

    if signal["score"] < MIN_SCORE:
        if diagnostics is not None:
            diagnostics["MIN_SCORE"] += 1
        return None

    entry = signal["entry"]

    if signal["direction"] == "LONG":
        tp1 = entry * 1.005
        tp2 = entry * 1.007
        invalidation = signal["exhaustion"]["low"]
    else:
        tp1 = entry * 0.995
        tp2 = entry * 0.993
        invalidation = signal["exhaustion"]["high"]

    signal["symbol"] = symbol
    signal["tp1"] = tp1
    signal["tp2"] = tp2
    signal["invalidation"] = invalidation
    signal["tf"] = "5m → 10m → 15m → 1h"

    if diagnostics is not None:
        diagnostics["SIGNAL"] += 1

    return signal


def fmt_price(x):
    if x >= 1000:
        return f"{x:.2f}"
    if x >= 100:
        return f"{x:.3f}"
    if x >= 10:
        return f"{x:.4f}"
    if x >= 1:
        return f"{x:.5f}"
    return f"{x:.8f}"


def build_message(signal):
    direction = signal["direction"]

    if direction == "LONG":
        icon = "🟢"
        side = "LONG"
    else:
        icon = "🔴"
        side = "SHORT"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    h = signal["higher"]

    reasons = ", ".join(signal["reasons"])

    return (
        f"{icon} СИГНАЛ {side}\n"
        f"━━━━━━━━━━━━━━\n"
        f"Монета: {signal['symbol']}\n"
        f"TF: {signal['tf']}\n"
        f"Score: {signal['score']}/100\n\n"
        f"Вхід: {fmt_price(signal['entry'])}\n"
        f"TP1 +0.50%: {fmt_price(signal['tp1'])}\n"
        f"TP2 +0.70%: {fmt_price(signal['tp2'])}\n"
        f"Invalidation: {fmt_price(signal['invalidation'])}\n\n"
        f"Патерн: {signal['run_len']} однакових → "
        f"виснаження → confirmation\n"
        f"MA5: {fmt_price(signal['ma5'])}\n"
        f"MA14: {fmt_price(signal['ma14'])}\n"
        f"MA30: {fmt_price(signal['ma30'])}\n"
        f"ATR(14): {signal['atr_pct'] * 100:.2f}%\n\n"
        f"15m: {h['state15']} ({h['score15']}/3)\n"
        f"1h: {h['state1h']} ({h['score1h']}/3)\n"
        f"Причини: {reasons}\n\n"
        f"Час: {now}\n"
        f"⚠️ Це технічний сигнал бота, не гарантія результату."
    )


def should_send(signal):
    key = (
        signal["symbol"],
        signal["tf"],
        signal["direction"],
    )

    candle_time = signal["confirm_time"]
    now = time.time()

    # Не повторювати ту саму confirmation candle.
    if last_sent.get(key) == candle_time:
        return False

    # Додатковий cooldown.
    if now - last_sent_time.get(key, 0) < COOLDOWN_MINUTES * 60:
        return False

    last_sent[key] = candle_time
    last_sent_time[key] = now
    return True


# ============================================================
#                 SCANNER
# ============================================================

def choose_symbols():
    symbols = get_contracts()

    if not symbols:
        print("[ERROR] Не вдалося отримати список контрактів.")
        return []

    # Якщо 0 — буквально всі.
    if MAX_SYMBOLS == 0:
        return symbols

    tickers = get_tickers()

    if tickers:
        symbols = sorted(
            symbols,
            key=lambda s: tickers.get(s, 0),
            reverse=True,
        )

    return symbols[:MAX_SYMBOLS]


def scan_one(symbol, diagnostics=None):
    try:
        return make_signal(symbol, diagnostics)
    except Exception as e:
        print(f"[SCAN ERROR] {symbol}: {e}")
        return None


def run_scan():
    start = time.time()

    symbols = choose_symbols()

    if not symbols:
        return

    print(
        f"\n[{datetime.now().strftime('%H:%M:%S')}] "
        f"Сканую {len(symbols)} контрактів..."
    )

    found = 0
    diagnostics = {key: 0 for key in DIAG_ORDER}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(scan_one, symbol): symbol
            for symbol in symbols
        }

        for future in as_completed(futures):
            symbol = futures[future]

            try:
                # scan_one нижче викликає make_signal без diagnostics,
                # тому для діагностики виконуємо безпосередньо тут.
                signal = make_signal(symbol, diagnostics)
            except Exception as e:
                print(f"[WORKER ERROR] {symbol}: {e}")
                continue

            if not signal:
                continue

            found += 1

            if should_send(signal):
                msg = build_message(signal)
                print("\n" + msg + "\n")
                telegram_send(msg)

    elapsed = time.time() - start

    if DIAGNOSTICS_ENABLED:
        diag_text = " | ".join(
            f"{key}={diagnostics[key]}" for key in DIAG_ORDER
        )
        print(f"[DIAG] {diag_text}")

    print(
        f"[DONE] Кандидатів: {found} | "
        f"Час циклу: {elapsed:.1f} сек."
    )


# ============================================================
#                 MAIN
# ============================================================

def main():
    print("==============================================")
    print(" MEXC FUTURES SIGNAL BOT")
    print(" 5m -> 10m -> 15m -> 1h")
    print(" Telegram alerts only")
    print("==============================================")

    if "ВСТАВ" in TELEGRAM_BOT_TOKEN:
        print("\n[!] Спочатку встав TELEGRAM_BOT_TOKEN")
    if "ВСТАВ" in str(TELEGRAM_CHAT_ID):
        print("[!] Спочатку встав TELEGRAM_CHAT_ID\n")

    if SEND_STARTUP_MESSAGE:
        telegram_send(
            "🤖 MEXC Signal Bot запущений.\n"
            "Сканування: MEXC Futures\n"
            "Патерн: 4-6 однакових → виснаження → confirmation\n"
            "TP1: +0.50%\n"
            "TP2: +0.70%\n"
            f"MIN_SCORE: {MIN_SCORE}/100"
        )

    while True:
        try:
            started = time.time()

            run_scan()

            elapsed = time.time() - started
            sleep_for = max(5, SCAN_SECONDS - elapsed)

            print(f"[WAIT] Наступний цикл через {sleep_for:.1f} сек.")
            time.sleep(sleep_for)

        except KeyboardInterrupt:
            print("\n[STOP] Бот зупинений.")
            break

        except Exception as e:
            print(f"[MAIN ERROR] {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()