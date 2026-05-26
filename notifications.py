"""
notifications.py — Email notification system.

Sends:
  - Daily signal report (rich HTML)
  - Safety / no-signal / circuit-breaker alerts
  - Weekly performance summary (every Sunday)
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

import pandas as pd

from utils import get_logger

logger = get_logger(__name__)


# ── Core send function ─────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str, text_body: str,
               cfg: dict, dry_run: bool = False) -> bool:
    """
    Send a multipart (plain + HTML) email via Gmail SMTP-SSL.
    Returns True on success, False on failure.
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would send email: '{subject}'")
        print(f"\n{'='*60}\nSUBJECT: {subject}\n{'='*60}\n{text_body}\n")
        return True

    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_PASSWORD")
    receiver = os.environ.get("EMAIL_RECEIVER")

    if not all([sender, password, receiver]):
        logger.error("Email credentials missing. Cannot send report.")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = f"Sniper Trading Bot <{sender}>"
    msg["To"] = receiver
    msg["Subject"] = subject

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        ec = cfg["email"]
        with smtplib.SMTP_SSL(ec["smtp_host"], ec["smtp_port"]) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        logger.info("📧 Email sent successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


# ── HTML template helpers ──────────────────────────────────────────────────────

_HTML_STYLE = """
<style>
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; padding: 20px; }
  .card { background: white; border-radius: 10px; padding: 20px; margin-bottom: 16px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: white; border-radius: 10px; padding: 24px; margin-bottom: 20px; }
  .header h1 { margin: 0; font-size: 22px; }
  .header p  { margin: 6px 0 0; opacity: 0.7; font-size: 13px; }
  .meta-row  { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
  .meta-pill { background: #e8f4fd; color: #1565c0; border-radius: 20px;
               padding: 4px 14px; font-size: 12px; font-weight: 600; }
  .meta-pill.warn { background: #fff3cd; color: #856404; }
  .meta-pill.ok   { background: #d4edda; color: #155724; }
  table  { width: 100%; border-collapse: collapse; font-size: 13px; }
  th     { background: #1a1a2e; color: white; padding: 10px 12px; text-align: left; }
  td     { padding: 9px 12px; border-bottom: 1px solid #eee; }
  tr:last-child td { border-bottom: none; }
  tr:nth-child(even) td { background: #f9f9f9; }
  .badge { border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700; }
  .badge-pullback    { background: #cce5ff; color: #004085; }
  .badge-breakout    { background: #d4edda; color: #155724; }
  .badge-mean_rev    { background: #fff3cd; color: #856404; }
  .badge-52w         { background: #f8d7da; color: #721c24; }
  .conf-high  { color: #28a745; font-weight: 700; }
  .conf-mid   { color: #fd7e14; font-weight: 700; }
  .conf-low   { color: #dc3545; font-weight: 600; }
  .section-title { font-size: 16px; font-weight: 700; color: #1a1a2e;
                   margin: 0 0 14px; padding-bottom: 8px; border-bottom: 2px solid #e0e0e0; }
  .pnl-pos { color: #28a745; font-weight: 700; }
  .pnl-neg { color: #dc3545; font-weight: 700; }
  .footer  { text-align: center; color: #aaa; font-size: 11px; margin-top: 20px; }
</style>
"""


def _conf_class(score: int) -> str:
    if score >= 8:
        return "conf-high"
    if score >= 6:
        return "conf-mid"
    return "conf-low"


def _badge_class(logic: str) -> str:
    mapping = {
        "PULLBACK": "badge-pullback",
        "BREAKOUT": "badge-breakout",
        "MEAN_REVERSION": "badge-mean_rev",
        "52W_HIGH": "badge-52w",
    }
    return mapping.get(logic, "badge-pullback")


# ── Daily signal email ─────────────────────────────────────────────────────────

def build_signal_email(signals: list[dict], date_str: str,
                       regime: str, vix: Optional[float],
                       breadth_pct: Optional[float]) -> tuple[str, str]:
    """Return (html_body, plain_text_body) for the daily signal email."""

    # ── Metadata pills ──
    regime_label = regime.upper()
    vix_str = f"{vix:.1f}" if vix else "N/A"
    breadth_str = f"{breadth_pct:.1f}%" if breadth_pct else "N/A"

    regime_class = "ok" if regime == "bull" else "warn"
    vix_class = "warn" if (vix or 0) > 20 else "ok"

    pills = f"""
    <div class="meta-row">
      <span class="meta-pill {regime_class}">📊 Regime: {regime_label}</span>
      <span class="meta-pill {vix_class}">🌊 India VIX: {vix_str}</span>
      <span class="meta-pill">🔭 Breadth: {breadth_str}</span>
      <span class="meta-pill ok">🎯 Signals: {len(signals)}</span>
    </div>
    """

    # ── Signal table ──
    rows = ""
    for s in signals:
        logic = s["logic"]
        badge = f'<span class="badge {_badge_class(logic)}">{logic}</span>'
        conf_cls = _conf_class(s["confidence"])
        rr = f"{s['rr_ratio']:.1f}" if s.get("rr_ratio") else "—"
        target_disp = s.get("target_display", "—")

        rows += f"""
        <tr>
          <td><strong>{s['ticker']}</strong></td>
          <td>{badge}</td>
          <td>₹{s['buy']:.2f}</td>
          <td>₹{s['sl']:.2f}</td>
          <td>{s['qty']}</td>
          <td>₹{s['pos_value']:,.0f}</td>
          <td>{target_disp}</td>
          <td class="{conf_cls}">{s['confidence']}/10</td>
          <td>{rr}</td>
        </tr>
        """

    table = f"""
    <table>
      <thead>
        <tr>
          <th>Stock</th><th>Strategy</th><th>Buy</th><th>Stop Loss</th>
          <th>Qty</th><th>Value</th><th>Target</th><th>Confidence</th><th>R:R</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """

    html = f"""
    <html><head>{_HTML_STYLE}</head><body>
      <div class="header">
        <h1>🎯 Sniper Trading Report</h1>
        <p>{date_str} &nbsp;·&nbsp; {len(signals)} high-conviction signal(s)</p>
      </div>
      {pills}
      <div class="card">
        <p class="section-title">Today's Signals</p>
        {table}
      </div>
      <div class="footer">
        This is an automated scan. Always do your own due diligence before trading.<br>
        Past performance does not guarantee future results.
      </div>
    </body></html>
    """

    # Plain text fallback
    plain = f"SNIPER TRADING REPORT: {date_str}\n{'='*50}\n"
    plain += f"Regime: {regime_label} | VIX: {vix_str} | Breadth: {breadth_str}\n\n"
    for s in signals:
        plain += (
            f"STOCK: {s['ticker']} ({s['logic']})\n"
            f"  Buy: ₹{s['buy']:.2f}  |  SL: ₹{s['sl']:.2f}  |  "
            f"Qty: {s['qty']}  |  Target: {s.get('target_display','—')}\n"
            f"  Confidence: {s['confidence']}/10  |  R:R: {s.get('rr_ratio',0):.1f}\n"
            f"{'-'*50}\n"
        )

    return html, plain


# ── Safety / status emails ─────────────────────────────────────────────────────

def build_safety_email(reason: str, date_str: str,
                       vix: Optional[float] = None) -> tuple[str, str]:
    """Build a safety-mode notification email."""
    messages = {
        "safety_off": (
            "🛡️ Nifty 50 Safety Mode Active",
            "Nifty 50 has dropped below its 50-EMA. All new entries have been paused "
            "to protect capital. The system will resume scanning automatically when "
            "the market recovers above the EMA."
        ),
        "bear": (
            "🐻 Bear Market Mode Active",
            "Market breadth has fallen below 40% — fewer than 4 in 10 Nifty 500 "
            "stocks are above their 200-day SMA. All new entries are paused."
        ),
        "circuit_breaker": (
            "🛑 Monthly Drawdown Circuit Breaker Tripped",
            "The cumulative monthly loss has exceeded the configured limit. "
            "No new entries will be made for the rest of this month."
        ),
        "no_signals": (
            "📉 No Signals Today",
            "The broad market is healthy, but no individual stocks met the "
            "high-conviction criteria today. The scan will run again tomorrow."
        ),
    }
    title, body = messages.get(reason, ("ℹ️ Market Scan Update", reason))
    vix_line = f"<p><strong>India VIX:</strong> {vix:.1f}</p>" if vix else ""

    html = f"""
    <html><head>{_HTML_STYLE}</head><body>
      <div class="header"><h1>{title}</h1><p>{date_str}</p></div>
      <div class="card">
        <p>{body}</p>
        {vix_line}
      </div>
      <div class="footer">Sniper Trading Bot — Automated Alert</div>
    </body></html>
    """
    plain = f"{title}\n{date_str}\n\n{body}"
    return html, plain


# ── Weekly performance summary ─────────────────────────────────────────────────

def build_weekly_summary_email(df: pd.DataFrame,
                                date_str: str) -> tuple[str, str]:
    """
    Build a weekly performance summary from the DB query result.
    df columns: date, ticker, wallet, logic, buy, sl, qty, confidence,
                rr_ratio, result, pnl
    """
    if df.empty:
        html = f"<html><body><p>No signals this week ({date_str}).</p></body></html>"
        return html, f"No signals this week ({date_str})."

    closed = df[df["result"].notna() & (df["result"] != "OPEN")]
    wins = (closed["result"] == "TARGET_HIT").sum()
    losses = (closed["result"] == "SL_HIT").sum()
    total_closed = len(closed)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
    total_pnl = closed["pnl"].sum() if not closed.empty else 0

    summary_pills = f"""
    <div class="meta-row">
      <span class="meta-pill ok">✅ Wins: {wins}</span>
      <span class="meta-pill warn">❌ Losses: {losses}</span>
      <span class="meta-pill">📊 Win Rate: {win_rate:.0f}%</span>
      <span class="meta-pill {'ok' if total_pnl >= 0 else 'warn'}">
        💰 P&L: ₹{total_pnl:+,.0f}
      </span>
    </div>
    """

    rows = ""
    for _, r in df.iterrows():
        result = r.get("result") or "OPEN"
        pnl = r.get("pnl", 0) or 0
        pnl_cls = "pnl-pos" if pnl >= 0 else "pnl-neg"
        result_icon = {"TARGET_HIT": "✅", "SL_HIT": "❌", "OPEN": "🔄"}.get(result, "—")

        rows += f"""
        <tr>
          <td>{r['date']}</td>
          <td><strong>{r['ticker']}</strong></td>
          <td><span class="badge {_badge_class(r['logic'])}">{r['logic']}</span></td>
          <td>₹{r['buy']:.2f}</td>
          <td>{r['confidence']}/10</td>
          <td>{result_icon} {result}</td>
          <td class="{pnl_cls}">₹{pnl:+,.2f}</td>
        </tr>
        """

    table = f"""
    <table>
      <thead>
        <tr><th>Date</th><th>Stock</th><th>Strategy</th><th>Buy</th>
            <th>Conf</th><th>Result</th><th>P&L</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """

    html = f"""
    <html><head>{_HTML_STYLE}</head><body>
      <div class="header">
        <h1>📅 Weekly Performance Summary</h1>
        <p>Week ending {date_str}</p>
      </div>
      {summary_pills}
      <div class="card">
        <p class="section-title">Signal Detail</p>
        {table}
      </div>
      <div class="footer">Sniper Trading Bot — Weekly Digest</div>
    </body></html>
    """

    plain = (
        f"WEEKLY SUMMARY — {date_str}\n{'='*50}\n"
        f"Wins: {wins}  Losses: {losses}  Win Rate: {win_rate:.0f}%\n"
        f"Total P&L: ₹{total_pnl:+,.0f}\n"
    )

    return html, plain
