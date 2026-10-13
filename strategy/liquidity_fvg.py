from strategy import liquidity_fvg, fibZZ10

STRATEGIES = {
    "fibZZ10": fibZZ10.analyze,
    "liquidity_fvg": liquidity_fvg.analyze,
}