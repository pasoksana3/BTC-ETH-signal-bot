# MEXC ENTRY POINT BOT — 30 COINS

Окремий Telegram-бот для пошуку точки входу на MEXC Futures.

Логіка:
1. 1H визначає напрямок структури.
2. 1H шукає supply/demand zone.
3. 15m чекає sweep ліквідності.
4. Після sweep потрібен CHoCH/BOS.
5. Шукається POI/FVG.
6. Розраховуються Entry, SL, TP1, TP2 і RR.
7. Сигнал відправляється в Telegram тільки після проходження всіх фільтрів.

30 монет:
BTC ETH SOL XRP DOGE SUI AVAX LINK DOT LTC
APT ARB OP PEPE WIF INJ FIL ATOM UNI TON
SEI TRX NEAR PYTH ADA ENA BNB BCH ETC XLM

ВАЖЛИВО:
- Бот НЕ відкриває угоди і НЕ має торгового API-ключа.
- Telegram token не вшитий у код навмисно. Старий токен, який був опублікований у чаті, вважай скомпрометованим і створи новий через BotFather.
- TELEGRAM_CHAT_ID вже заданий: -5417788354.
- MEXC Futures API зараз використовує https://api.mexc.com.

Railway:
1. Завантаж цей ZIP у новий service.
2. Variables -> додай TELEGRAM_BOT_TOKEN зі свіжим токеном BotFather.
3. TELEGRAM_CHAT_ID залиш -5417788354.
4. Start command: python main.py

Перш ніж використовувати сигнали на реальних грошах, перевір їх на історичних даних/демо-режимі. Стратегія не гарантує прибуток.

### Rate-limit protection
The bot scans sequentially and spaces public MEXC requests by 1.2 seconds. HTTP 510 triggers 8/16/24/32-second backoff retries.


### Diagnostic mode
Every scan now logs the first filter that rejected each symbol: `1H_BIAS`, `HTF_ZONE_DISTANCE`, `LIQUIDITY_SWEEP`, `CHOCH_BOS`, `POI_FVG`, `ENTRY_DISTANCE_ATR`, `RR`, etc. The final scan line includes an aggregated filter breakdown. This does not send diagnostic messages to Telegram.


### V2 adaptive HTF zone distance
The diagnostic build now uses an adaptive 2.0-3.0 ATR envelope around the 1H HTF zone instead of the fixed 1.5 ATR distance. The rest of the confirmation chain remains intact: liquidity sweep -> CHoCH/BOS -> POI/FVG -> entry distance -> RR.

Environment overrides:
- `ZONE_DISTANCE_ATR_MIN=2.0`
- `ZONE_DISTANCE_ATR_MAX=3.0`
