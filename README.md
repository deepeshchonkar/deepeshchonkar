# 🎯 Claude Trading Bot

Automated NSE stock scanner with multi-strategy signal generation, dynamic risk management,
outcome tracking, backtesting, and rich HTML email reports. Runs free on GitHub Actions.

---

## Project Structure

```
sniper_bot/
├── config.yaml              ← All tuneable parameters (no code changes needed)
├── main.py                  ← Entry point + CLI
├── scanner.py               ← Scan orchestration
├── strategies.py            ← PULLBACK, BREAKOUT, MEAN_REVERSION, 52W_HIGH
├── indicators.py            ← SMA, EMA, ATR, RSI, Bollinger, ADX, etc.
├── risk.py                  ← Position sizing, R:R filter, circuit breaker
├── data.py                  ← NSE ticker fetch, yfinance OHLCV, VIX, breadth
├── database.py              ← SQLite persistence (signals, outcomes, runs)
├── tracker.py               ← Next-day outcome tracker
├── notifications.py         ← HTML email reports + weekly summary
├── backtester.py            ← Vectorized backtesting
├── requirements.txt
├── tests/
│   └── test_strategies.py   ← pytest unit tests
└── .github/
    └── workflows/
        └── daily_scan.yml   ← GitHub Actions scheduler
```

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set environment variables
```bash
export EMAIL_SENDER="your@gmail.com"
export EMAIL_PASSWORD="your-app-password"   # Gmail App Password
export EMAIL_RECEIVER="alerts@yourmail.com"
```

### 3. Run a dry-run (no email, no DB write)
```bash
python main.py --dry-run
```

### 4. Run the full daily scan
```bash
python main.py
```

### 5. Other commands
```bash
python main.py --track                          # Review yesterday's outcomes
python main.py --weekly-summary                 # Send weekly performance email
python main.py --backtest                       # Backtest all wallets
python main.py --backtest --wallet NIFTY_PULLBACK --start 2023-01-01
python main.py --dry-run                        # Test without side effects
```

### 6. Run tests
```bash
pytest tests/ -v
```

---

## GitHub Actions (Free Automation)

1. Push this repo to GitHub
2. Add the three `EMAIL_*` secrets under **Settings → Secrets → Actions**
3. The workflow runs automatically:
   - **09:15 IST weekdays** — daily scan
   - **16:00 IST weekdays** — outcome tracker
   - **Sunday 08:00 IST** — weekly summary
4. Trigger manually any time via **Actions → Run workflow**

---

## Strategies

| Strategy | Logic | Regime |
|---|---|---|
| PULLBACK | Uptrend + EMA-50 touch + green candle + RSI 40-65 | Bull + Mixed |
| BREAKOUT | 20-day high breakout + volume surge + RSI > 55 | Bull only |
| MEAN_REVERSION | RSI < 35 + lower BB touch + reversal candle | Bull + Mixed |
| 52W_HIGH | Fresh 52-week high + 1.5× volume + ADX > 25 | Bull only |

---

## Risk Controls

- **Dynamic sizing**: position size scales with India VIX
- **Minimum R:R**: signals rejected if reward < 2× risk (configurable)
- **Portfolio cap**: max 60% of total capital in open trades
- **Sector cap**: max 3 signals per wallet/sector per day
- **Alert cooldown**: same stock suppressed for 5 days after a signal
- **Circuit breaker**: scanning paused if monthly loss > 5% of capital
- **Macro guard**: all entries paused if Nifty drops below 50-EMA
- **Bear mode**: all entries paused if < 40% of Nifty 500 above 200 DMA

---

## Configuration

All parameters are in `config.yaml`. Key sections:

```yaml
risk:
  base_risk_pct: 0.01        # 1% of wallet cap risked per trade
  min_rr_ratio: 2.0          # Minimum reward:risk ratio
  monthly_drawdown_limit_pct: 0.05  # 5% monthly loss limit

filters:
  min_confidence_score: 5    # Signal must score 5+/10 to be emitted

app:
  dry_run: false             # Set true to test without side effects
  alert_cooldown_days: 5     # Days before same stock can re-signal
```

---

## Disclaimer

This is an educational tool. Always do your own due diligence before trading.
Past performance does not guarantee future results. Not SEBI-registered advice.
