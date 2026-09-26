"""Live/backtest signal generator for the Keltrader BB-Squeeze strategy.

The original public repository contains this module as redacted. This implementation
reconstructs the documented strategy interface from technical.py, README.md and the
backtester/optimizer call sites. It is not the author's private implementation.
"""
from dataclasses import dataclass
from typing import Dict, Optional, List
import pandas as pd
from technical import BBSqueezeAnalyzer

@dataclass
class TradeSignal:
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    position_size: float = 0.40
    reasons: List[str] = None
    timestamp: object = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []

class BBSqueezeSignalGenerator:
    def __init__(self, analyzer: Optional[BBSqueezeAnalyzer] = None,
                 min_squeeze_bars: int = 3, min_volume_ratio: float = 1.2,
                 rsi_overbought: float = 70, rsi_oversold: float = 30,
                 atr_stop_mult: float = 2.0, atr_target_mult: float = 3.0,
                 base_position: float = 0.40, min_position: float = 0.30,
                 max_position: float = 0.90, setup_validity_bars: int = 5,
                 signal_timeframe_minutes: int = 15):
        self.analyzer = analyzer or BBSqueezeAnalyzer()
        self.min_squeeze_bars = min_squeeze_bars
        self.min_volume_ratio = min_volume_ratio
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.base_position = base_position
        self.min_position = min_position
        self.max_position = max_position
        self.setup_validity_bars = setup_validity_bars
        self.signal_timeframe_minutes = signal_timeframe_minutes
        self.active_setups: Dict[str, dict] = {}
        self.consecutive_losses = 0
        self.signal_data = {}
        self.atr_data = {}

    def set_signal_data(self, data): self.signal_data = data
    def set_atr_data(self, data): self.atr_data = data

    def generate_signal(self, df: pd.DataFrame, symbol: str = '', ts=None) -> TradeSignal:
        if df is None or len(df) < 60:
            return TradeSignal('neutral', 0, 0, 0, 0, 0, ['insufficient_data'], ts)
        work = self.analyzer.calculate_indicators(df)
        breakout = self.analyzer.detect_breakout(work, self.min_squeeze_bars, self.min_volume_ratio)
        if not breakout:
            return TradeSignal('neutral', 0, 0, 0, 0, 0, ['no_breakout'], ts)

        direction = breakout['direction']
        rsi = float(breakout['rsi']) if pd.notna(breakout['rsi']) else 50.0
        if direction == 'long' and rsi >= self.rsi_overbought:
            return TradeSignal('neutral', 0, 0, 0, float(breakout['atr'] or 0), 0, ['rsi_overbought'], ts)
        if direction == 'short' and rsi <= self.rsi_oversold:
            return TradeSignal('neutral', 0, 0, 0, float(breakout['atr'] or 0), 0, ['rsi_oversold'], ts)

        entry = float(breakout['price'])
        atr = float(breakout['atr'])
        if atr <= 0 or entry <= 0:
            return TradeSignal('neutral', 0, 0, 0, atr, 0, ['invalid_atr'], ts)
        if direction == 'long':
            sl = entry - self.atr_stop_mult * atr
            tp = entry + self.atr_target_mult * atr
        else:
            sl = entry + self.atr_stop_mult * atr
            tp = entry - self.atr_target_mult * atr
        reasons = [f'squeeze={int(breakout["squeeze_bars"])}',
                   f'volume={breakout["volume_ratio"]:.2f}x',
                   f'rsi={rsi:.1f}', f'momentum={breakout["momentum"]:.2f}ATR']
        return TradeSignal(direction, entry, sl, tp, atr, self.base_position, reasons, ts)

    def record_trade_result(self, net: float):
        self.consecutive_losses = self.consecutive_losses + 1 if net < 0 else 0
