"""Keltrader live signal bot: MEXC market data -> BB Squeeze -> Telegram.

Alert-only: this file does NOT place exchange orders.
The strategy implementation in signal_generator.py reconstructs the documented
public strategy because the original live/signal modules were redacted.
"""
import os, time, logging
from datetime import datetime, timezone
import requests
import pandas as pd
from technical import BBSqueezeAnalyzer
from signal_generator import BBSqueezeSignalGenerator
from telegram_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger('keltrader-live')

MEXC_URL = 'https://api.mexc.com/api/v3/klines'
TIMEFRAME = os.getenv('SIGNAL_TIMEFRAME', '15m')
LIMIT = int(os.getenv('KLINE_LIMIT', '250'))
POLL_SECONDS = int(os.getenv('POLL_SECONDS', '60'))
MIN_SQUEEZE_BARS = int(os.getenv('MIN_SQUEEZE_BARS', '3'))
MIN_VOLUME_RATIO = float(os.getenv('MIN_VOLUME_RATIO', '1.2'))
RSI_OVERBOUGHT = float(os.getenv('RSI_OVERBOUGHT', '70'))
RSI_OVERSOLD = float(os.getenv('RSI_OVERSOLD', '30'))
ATR_STOP_MULT = float(os.getenv('ATR_STOP_MULT', '2.0'))
ATR_TARGET_MULT = float(os.getenv('ATR_TARGET_MULT', '3.0'))

# The public Jicheolha README reports these core assets.
SYMBOLS = [s.strip().upper() for s in os.getenv(
    'SYMBOLS', 'BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT'
).split(',') if s.strip()]

analyzer = BBSqueezeAnalyzer()
generator = BBSqueezeSignalGenerator(
    analyzer=analyzer,
    min_squeeze_bars=MIN_SQUEEZE_BARS,
    min_volume_ratio=MIN_VOLUME_RATIO,
    rsi_overbought=RSI_OVERBOUGHT,
    rsi_oversold=RSI_OVERSOLD,
    atr_stop_mult=ATR_STOP_MULT,
    atr_target_mult=ATR_TARGET_MULT,
    signal_timeframe_minutes=15,
)

session = requests.Session()
session.headers.update({'User-Agent': 'Keltrader-Live/1.0'})
last_alert = {}

def fetch_klines(symbol):
    r = session.get(MEXC_URL, params={'symbol': symbol, 'interval': TIMEFRAME, 'limit': LIMIT}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or len(data) < 60:
        raise RuntimeError(f'bad kline response for {symbol}')
    # MEXC: open time, open, high, low, close, volume, close time, ...
    df = pd.DataFrame(data, columns=['time','open','high','low','close','volume','close_time','qvol','trades','tb_base','tb_quote','ignore'])
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
    # Exclude the currently forming candle. Signals are based on closed bars only.
    if len(df) > 1:
        df = df.iloc[:-1].copy()
    return df

def telegram(text):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    r = session.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': text}, timeout=15)
    r.raise_for_status()

def fmt_price(x):
    if x >= 1000: return f'{x:.2f}'
    if x >= 1: return f'{x:.5f}'
    if x >= 0.01: return f'{x:.6f}'
    return f'{x:.8f}'

def make_message(symbol, signal):
    side = '🟢 LONG' if signal.direction == 'long' else '🔴 SHORT'
    risk = abs(signal.entry_price - signal.stop_loss) / signal.entry_price * 100
    reward = abs(signal.take_profit - signal.entry_price) / signal.entry_price * 100
    rr = reward / risk if risk else 0
    return (
        '🎯 KELTRADER SIGNAL\n\n'
        f'{side}  {symbol}\n\n'
        f'Entry: {fmt_price(signal.entry_price)}\n'
        f'SL:    {fmt_price(signal.stop_loss)}  ({risk:.2f}%)\n'
        f'TP:    {fmt_price(signal.take_profit)}  ({reward:.2f}%)\n'
        f'RR:    1:{rr:.2f}\n\n'
        'Strategy: BB Squeeze → breakout\n'
        + '\n'.join(f'• {x}' for x in signal.reasons)
        + '\n\nMode: alert-only'
    )

def scan_once():
    signals = 0
    for symbol in SYMBOLS:
        try:
            df = fetch_klines(symbol)
            candle_id = str(df.iloc[-1]['time'])
            signal = generator.generate_signal(df, symbol, df.iloc[-1]['time'])
            if signal.direction == 'neutral':
                continue
            key = f'{symbol}:{candle_id}:{signal.direction}'
            if last_alert.get(symbol) == key:
                continue
            telegram(make_message(symbol, signal))
            last_alert[symbol] = key
            signals += 1
            log.info('SIGNAL %s %s entry=%s sl=%s tp=%s', signal.direction.upper(), symbol, signal.entry_price, signal.stop_loss, signal.take_profit)
        except Exception as e:
            log.warning('ERROR %s | %s', symbol, e)
        time.sleep(0.35)
    return signals

def main():
    log.info('KELTRADER LIVE started | %d symbols | timeframe=%s | poll=%ss', len(SYMBOLS), TIMEFRAME, POLL_SECONDS)
    log.info('Mode=alert-only | MEXC=%s', MEXC_URL)
    while True:
        started = time.time()
        try:
            count = scan_once()
            log.info('scan complete | %.1fs | signals=%d', time.time() - started, count)
        except Exception as e:
            log.exception('scan failed: %s', e)
        time.sleep(max(1, POLL_SECONDS - (time.time() - started)))

if __name__ == '__main__':
    main()
