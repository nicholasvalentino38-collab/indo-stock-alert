
import os
import math
from datetime import datetime, timezone
from typing import List, Dict, Any

import pandas as pd
import requests
import yfinance as yf


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_str(name, str(default)))
    except Exception:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(env_str(name, str(default))))
    except Exception:
        return default


TELEGRAM_BOT_TOKEN = env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env_str("TELEGRAM_CHAT_ID")
TICKERS = [t.strip().upper().replace(".JK", "") for t in env_str(
    "TICKERS", "BBCA,BBRI,BMRI,TLKM,ASII"
).split(",") if t.strip()]

MIN_RVOL = env_float("MIN_RVOL", 3.0)
MIN_PRICE_CHANGE_PCT = env_float("MIN_PRICE_CHANGE_PCT", 0.8)
MIN_BAR_VALUE_IDR = env_float("MIN_BAR_VALUE_IDR", 1_000_000_000)
LOOKBACK_BARS = env_int("LOOKBACK_BARS", 20)
BAR_INTERVAL = env_str("BAR_INTERVAL", "5m")


def validate_config() -> None:
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    if BAR_INTERVAL not in {"1m", "2m", "5m", "15m"}:
        raise RuntimeError("BAR_INTERVAL must be one of: 1m, 2m, 5m, 15m")


def yahoo_period_for_interval(interval: str) -> str:
    return {"1m": "1d", "2m": "2d", "5m": "5d", "15m": "5d"}.get(interval, "5d")


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()


def fetch_intraday(symbol: str, interval: str) -> pd.DataFrame:
    ticker = yf.Ticker(f"{symbol}.JK")
    df = ticker.history(period=yahoo_period_for_interval(interval), interval=interval, auto_adjust=False, actions=False)
    if df.empty:
        return df
    df = df.reset_index()
    dt_col = "Datetime" if "Datetime" in df.columns else "Date"
    df.rename(columns={dt_col: "Datetime"}, inplace=True)
    df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True, errors="coerce")
    df = df.dropna(subset=["Datetime"]).copy()
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    return df


def analyze_symbol(symbol: str) -> Dict[str, Any] | None:
    df = fetch_intraday(symbol, BAR_INTERVAL)
    needed = LOOKBACK_BARS + 3
    if df.empty or len(df) < needed:
        return None

    recent = df.tail(LOOKBACK_BARS + 2).copy()
    last_bar = recent.iloc[-1]
    prev_bar = recent.iloc[-2]
    prior_window = recent.iloc[:-2]

    avg_vol = float(prior_window["Volume"].mean()) if len(prior_window) else 0.0
    last_vol = float(last_bar["Volume"])
    if avg_vol <= 0:
        return None

    rvol = last_vol / avg_vol
    prev_close = float(prev_bar["Close"])
    last_close = float(last_bar["Close"])
    if prev_close <= 0:
        return None

    price_change_pct = ((last_close / prev_close) - 1.0) * 100.0
    breakout_level = float(prior_window["High"].max()) if len(prior_window) else float("nan")
    breakout = bool(last_close > breakout_level) if not math.isnan(breakout_level) else False
    bar_value_idr = last_close * last_vol

    score = 0
    score += 40 if rvol >= MIN_RVOL else 20 if rvol >= MIN_RVOL * 0.8 else 0
    score += 30 if price_change_pct >= MIN_PRICE_CHANGE_PCT else 15 if price_change_pct >= MIN_PRICE_CHANGE_PCT * 0.6 else 0
    score += 20 if bar_value_idr >= MIN_BAR_VALUE_IDR else 10 if bar_value_idr >= MIN_BAR_VALUE_IDR * 0.5 else 0
    score += 10 if breakout else 0

    is_alert = (
        rvol >= MIN_RVOL
        and price_change_pct >= MIN_PRICE_CHANGE_PCT
        and bar_value_idr >= MIN_BAR_VALUE_IDR
    )

    ts_jakarta = last_bar["Datetime"].tz_convert("Asia/Jakarta")

    return {
        "symbol": symbol,
        "timestamp": ts_jakarta,
        "close": last_close,
        "rvol": rvol,
        "price_change_pct": price_change_pct,
        "bar_value_idr": bar_value_idr,
        "breakout": breakout,
        "score": score,
        "is_alert": is_alert,
    }


def format_currency_idr(x: float) -> str:
    return f"Rp{x:,.0f}".replace(",", ".")


def format_alert(row: Dict[str, Any]) -> str:
    breakout_text = "Ya" if row["breakout"] else "Tidak"
    ts_text = row["timestamp"].strftime("%Y-%m-%d %H:%M WIB")
    return (
        f"🚨 <b>INDO STOCK ALERT</b>\n"
        f"<b>{row['symbol']}</b>\n"
        f"Waktu: {ts_text}\n"
        f"Harga: {row['close']:.2f}\n"
        f"Perubahan bar: {row['price_change_pct']:.2f}%\n"
        f"RVOL: {row['rvol']:.2f}x\n"
        f"Nilai bar: {format_currency_idr(row['bar_value_idr'])}\n"
        f"Breakout: {breakout_text}\n"
        f"Skor: {row['score']}"
    )


def format_summary(results: List[Dict[str, Any]]) -> str:
    now_jkt = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M WIB")
    lines = [
        "📊 <b>Scan selesai</b>",
        f"Waktu: {now_jkt}",
        f"Jumlah ticker: {len(results)}",
    ]
    if not results:
        lines.append("Tidak ada data yang lolos pengecekan.")
        return "\n".join(lines)

    ranked = sorted(results, key=lambda x: (x["is_alert"], x["score"], x["rvol"]), reverse=True)
    lines.append("")
    lines.append("<b>Top radar:</b>")
    for row in ranked[:5]:
        flag = "ALERT" if row["is_alert"] else "WATCH"
        lines.append(
            f"- {row['symbol']} | {flag} | RVOL {row['rvol']:.2f}x | Δ {row['price_change_pct']:.2f}% | Skor {row['score']}"
        )
    return "\n".join(lines)


def main() -> None:
    validate_config()
    all_results: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []

    for symbol in TICKERS:
        try:
            result = analyze_symbol(symbol)
            if result:
                all_results.append(result)
                if result["is_alert"]:
                    alerts.append(result)
        except Exception as exc:
            print(f"[WARN] {symbol}: {exc}")

    if alerts:
        alerts = sorted(alerts, key=lambda x: (x["score"], x["rvol"]), reverse=True)
        for row in alerts:
            send_telegram_message(format_alert(row))
    else:
        print("No alerts found.")

    if env_str("SEND_SUMMARY", "1") == "1":
        send_telegram_message(format_summary(all_results))


if __name__ == "__main__":
    main()
