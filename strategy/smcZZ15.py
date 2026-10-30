from __future__ import annotations

from strategy._smc_zz_core import build_analyzer, format_signal as _format_signal

NAME = "SMC_ZZ15"
DESCRIPTION = "Smart Money: ZigZag 15% + Liquidity Sweep + BOS/CHoCH, 1H, RR 1:3"
ZIGZAG_DEVIATION = 15.0

analyze = build_analyzer(NAME, ZIGZAG_DEVIATION)


def format_signal(signal):
    return _format_signal(signal)
