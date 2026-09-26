# Jicheolha Crypto Trading Bot — Live Telegram signal layer

This package starts from the user's uploaded Jicheolha/Keltrader repository and adds a live, alert-only MEXC scanner plus Telegram delivery.

## Important provenance

The public repository contains `technical.py`, `backtester.py`, optimization code and documentation, but its original `signal_generator.py` and live trader are marked redacted. Therefore `signal_generator.py` and `live_bot.py` in this package are a reconstruction of the **documented strategy and interfaces**, not the author's private source code.

## What it does

- Reads closed MEXC candles.
- Uses the documented BB-inside-Keltner squeeze → release → volume confirmation → RSI filter.
- Calculates ATR-based stop loss and take profit using the documented default multipliers.
- Sends LONG/SHORT entry, SL, TP and RR to the configured Telegram group.
- Does **not** place exchange orders.
- Prevents duplicate alerts for the same closed candle.

## Run on Railway

Start command:

`python live_bot.py`

Dependencies are in `requirements.txt`.

Optional environment variables:

- `SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT`
- `SIGNAL_TIMEFRAME=15m`
- `POLL_SECONDS=60`
- `KLINE_LIMIT=250`
- `MIN_SQUEEZE_BARS=3`
- `MIN_VOLUME_RATIO=1.2`
- `RSI_OVERBOUGHT=70`
- `RSI_OVERSOLD=30`
- `ATR_STOP_MULT=2.0`
- `ATR_TARGET_MULT=3.0`

The Telegram credentials are stored in `telegram_config.py` because that was explicitly requested. If the bot token has been exposed publicly, rotate it in BotFather before using the bot with real funds.
