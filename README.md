# MEXC Futures Signal Bot

Telegram alert bot for MEXC USDT-M perpetual futures.

## What it does

- scans MEXC futures contracts;
- detects 4–6 same-direction candles followed by exhaustion and opposite confirmation;
- builds 10m candles locally from 5m data;
- checks 5m / 10m / 15m / 1h context;
- calculates TP1 at +0.50% and TP2 at +0.70%;
- sends LONG/SHORT alerts to Telegram;
- does **not** place trades.

> `85/100` is a technical pattern score, not an 85% probability.
> Historical hit-rate must be measured by backtesting.

## Files

- `mexc_signal_bot.py` — main scanner and Telegram bot
- `backtest.py` — historical validation engine
- `requirements.txt` — Python dependencies
- `.env.example` — configuration template
- `.gitignore` — GitHub ignore rules

## Quick start

### 1. Clone

```bash
git clone https://github.com/YOUR_USERNAME/mexc-signal-bot.git
cd mexc-signal-bot
```

### 2. Create virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

```powershell
.venv\Scripts\activate
```

### 3. Install

```bash
pip install -r requirements.txt
```

### 4. Configure

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Put your Telegram bot token and group chat ID into `.env`.

Never commit `.env` to GitHub.

### 5. Run

```bash
python mexc_signal_bot.py
```

## Backtest

The backtest is intended to answer the important question:

> Which exact candle patterns historically reached +0.50% or +0.70% after the signal?

Run:

```bash
python backtest.py --symbol BTC_USDT --days 30
```

For multiple symbols:

```bash
python backtest.py --symbols BTC_USDT,ETH_USDT,SOL_USDT,XRP_USDT --days 60
```

The report includes:

- number of signals;
- TP1 hits;
- TP2 hits;
- invalidation hits;
- unresolved signals;
- TP1 hit rate;
- TP2 hit rate;
- results grouped by run length;
- results grouped by score.

The report does not call a signal “85%” unless the measured historical sample actually supports that percentage.

## Recommended first live configuration

For the first test:

```text
SCAN_SECONDS=60
MAX_SYMBOLS=100
MIN_SCORE=85
```

After validating the signal statistics, `MAX_SYMBOLS=0` can be used to scan all USDT contracts.

## Risk

This project produces technical alerts only. It is not a guarantee of profit and does not execute trades.
