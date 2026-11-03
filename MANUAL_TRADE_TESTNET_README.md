# MASB Manual Futures TESTNET patch

This archive is a cleaned copy of the supplied MASB project with a manual LIMIT-order workflow added for **Binance Futures TESTNET only**. The module hardcodes `https://testnet.binancefuture.com`; it does not support production endpoints.

## Install
1. Extract the archive.
2. Install the project's dependencies from `requirements.txt`.
3. Copy `.env.testnet.example` to `.env` (or merge these variables into your existing local `.env`).
4. Create TESTNET API credentials from Binance Futures Testnet and set `BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_API_SECRET`. Never put production credentials here.
5. Set `MASB_TESTNET_MARGIN_USDT` to the simulated margin used for sizing.
6. Start MASB normally. The main menu includes `🧪 Trade TESTNET`.

## Flow
Coin -> LONG/SHORT -> Entry -> TP -> SL -> Leverage -> Confirm. Entry is LIMIT/GTC. Quantity is calculated from `MASB_TESTNET_MARGIN_USDT * leverage / entry`. The module rounds price and quantity using exchange filters. On confirmation it sends the entry order and conditional TP/SL orders to TESTNET. If protective-order setup fails, it attempts to cancel the entry.

## Important limitations
- TESTNET only; no real-money trading code is included in this manual module.
- Testnet symbols and conditional-order support may change; verify order responses in the testnet account.
- This add-on does not modify the existing automated strategy execution module.
- Use a fresh TESTNET API key; never share `.env`.
