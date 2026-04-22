import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import yfinance as yf


# =========================
# Helpers
# =========================

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


def parse_tickers(raw: str) -> List[str]:
    return [
        t.strip().upper().replace(".JK", "")
        for t in raw.split(",")
        if t.strip()
    ]


@dataclass
class GroupConfig:
    name: str
    tickers: List[str]
    min_rvol: float
    min_price_change_pct: float
    min_bar_value_idr: float


TELEGRAM_BOT_TOKEN = env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env_str("TELEGRAM_CHAT_ID")
LOOKBACK_BARS = env_int("LOOKBACK_BARS", 20)
BAR_INTERVAL = env_str("BAR_INTERVAL", "5m")
SEND_SUMMARY = env_str("SEND_SUMMARY", "0") == "1"
SEND_EMPTY_SUMMARY = env_str("SEND_EMPTY_SUMMARY", "0") == "1"
TOP_N_PER_GROUP = env_int("TOP_N_PER_GROUP", 3)

LARGE = GroupConfig(
    name="LARGE",
    tickers=parse_tickers(env_str(
        "LARGE_TICKERS",
        "BBCA,BBRI,BMRI,TLKM,ASII,ICBP,INDF,ADRO,UNTR,CPIN,ANTM,AMMN,PGAS,KLBF,BRPT,MDKA"
    )),
    min_rvol=env_float("LARGE_MIN_RVOL", 1.8),
    min_price_change_pct=env_float("LARGE_MIN_PRICE_CHANGE_PCT", 0.30),
    min_bar_value_idr=env_float("LARGE_MIN_BAR_VALUE_IDR", 2_000_000_000),
)

MID = GroupConfig(
    name="MID",
    tickers=parse_tickers(env_str(
        "MID_TICKERS",
        "MEDC,SMDR,ESSA,PGEO,TPIA,ISAT,EXCL,MAPI,ERAA,ACES,ITMG,HRUM,INKP,TKIM,JPFA,AKRA"
    )),
    min_rvol=env_float("MID_MIN_RVOL", 2.2),
    min_price_change_pct=env_float("MID_MIN_PRICE_CHANGE_PCT", 0.50),
    min_bar_value_idr=env_float("MID_MIN_BAR_VALUE_IDR", 750_000_000),
)

SMALL = GroupConfig(
    name="SMALL",
    tickers=parse_tickers(env_str(
        "SMALL_TICKERS",
        "WIFI,BRMS,GOTO,ARTO,BUKA,HEAL,SIDO,SCMA,MIKA,SPII,AVIA,DOID,ELSA,MYOR,PWON,CTRA"
    )),
    min_rvol=env_float("SMALL_MIN_RVOL", 3.0),
    min_price_change_pct=env_float("SMALL_MIN_PRICE_CHANGE_PCT", 0.80),
    min_bar_value_idr=env_float("SMALL_MIN_BAR_VALUE_IDR", 500_000_000),
)

GROUPS = [LARGE, MID, SMALL]


# =========================
# Validation
# =========================

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

    total_tickers = sum(len(g.tickers) for g in GROUPS)
    if total_tickers == 0:
        raise RuntimeError("No tickers configured.")


# =========================
# External services
# =========================

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
    df = ticker.history(
        period=yahoo_period_for_interval(interval),
        interval=interval,
        auto_adjust=False,
        actions=False,
    )
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


# =========================
# Analysis
# =========================

def calc_score(
    rvol: float,
    price_change_pct: float,
    bar_value_idr: float,
    breakout: bool,
    cfg: GroupConfig,
) -> int:
    score = 0
    score += 40 if rvol >= cfg.min_rvol else 20 if rvol >= cfg.min_rvol * 0.8 else 0
    score += 30 if price_change_pct >= cfg.min_price_change_pct else 15 if price_change_pct >= cfg.min_price_change_pct * 0.6 else 0
    score += 20 if bar_value_idr >= cfg.min_bar_value_idr else 10 if bar_value_idr >= cfg.min_bar_value_idR * 0.5 else 0
    score += 10 if breakout else 0
    return score


def analyze_symbol(symbol: str, cfg: GroupConfig) -> Optional[Dict[str, Any]]:
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
    score += 40 if rvol >= cfg.min_rvol else 20 if rvol >= cfg.min_rvol * 0.8 else 0
    score += 30 if price_change_pct >= cfg.min_price_change_pct else 15 if price_change_pct >= cfg.min_price_change_pct * 0.6 else 0
    score += 20 if bar_value_idr >= cfg.min_bar_value_idr else 10 if bar_value_idr >= cfg.min_bar_value_idr * 0.5 else 0
    score += 10 if breakout else 0

    is_alert = (
        rvol >= cfg.min_rvol
        and price_change_pct >= cfg.min_price_change_pct
        and bar_value_idr >= cfg.min_bar_value_idr
    )

    ts_jakarta = last_bar["Datetime"].tz_convert("Asia/Jakarta")

    return {
        "symbol": symbol,
        "group": cfg.name,
        "timestamp": ts_jakarta,
        "close": last_close,
        "rvol": rvol,
        "price_change_pct": price_change_pct,
        "bar_value_idr": bar_value_idr,
        "breakout": breakout,
        "score": score,
        "is_alert": is_alert,
    }


# =========================
# Formatting
# =========================

def format_currency_idr(x: float) -> str:
    if x >= 1_000_000_000:
        return f"Rp{x/1_000_000_000:.2f}B"
    if x >= 1_000_000:
        return f"Rp{x/1_000_000:.0f}M"
    return f"Rp{x:,.0f}".replace(",", ".")


def format_alert(row: Dict[str, Any]) -> str:
    breakout_text = "Ya" if row["breakout"] else "Tidak"
    ts_text = row["timestamp"].strftime("%Y-%m-%d %H:%M WIB")
    return (
        f"🚨 <b>INDO STOCK ALERT</b>\n"
        f"<b>{row['symbol']}</b> | {row['group']} CAP\n"
        f"Waktu: {ts_text}\n"
        f"Harga: {row['close']:.2f}\n"
        f"Perubahan bar: {row['price_change_pct']:.2f}%\n"
        f"RVOL: {row['rvol']:.2f}x\n"
        f"Nilai bar: {format_currency_idr(row['bar_value_idr'])}\n"
        f"Breakout: {breakout_text}\n"
        f"Skor: {row['score']}"
    )


def group_top_lines(results: List[Dict[str, Any]], group_name: str) -> List[str]:
    rows = [r for r in results if r["group"] == group_name]
    if not rows:
        return [f"<b>{group_name}</b>: tidak ada data"]
    ranked = sorted(rows, key=lambda x: (x["is_alert"], x["score"], x["rvol"]), reverse=True)
    lines = [f"<b>{group_name}</b>"]
    for row in ranked[:TOP_N_PER_GROUP]:
        flag = "ALERT" if row["is_alert"] else "WATCH"
        lines.append(
            f"- {row['symbol']} | {flag} | RVOL {row['rvol']:.2f}x | Δ {row['price_change_pct']:.2f}% | Skor {row['score']}"
        )
    return lines


def format_summary(results: List[Dict[str, Any]]) -> str:
    now_jkt = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M WIB")
    total_tickers = sum(len(g.tickers) for g in GROUPS)
    alert_count = sum(1 for r in results if r["is_alert"])

    lines = [
        "📊 <b>Scan selesai</b>",
        f"Waktu: {now_jkt}",
        f"Jumlah ticker: {total_tickers}",
        f"Jumlah alert: {alert_count}",
        "",
    ]
    lines.extend(group_top_lines(results, "LARGE"))
    lines.append("")
    lines.extend(group_top_lines(results, "MID"))
    lines.append("")
    lines.extend(group_top_lines(results, "SMALL"))
    return "\n".join(lines)


# =========================
# Main
# =========================

def main() -> None:
    validate_config()
    all_results: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []

    for cfg in GROUPS:
        for symbol in cfg.tickers:
            try:
                result = analyze_symbol(symbol, cfg)
                if result:
                    all_results.append(result)
                    if result["is_alert"]:
                        alerts.append(result)
            except Exception as exc:
                print(f"[WARN] {cfg.name}:{symbol}: {exc}")

    if alerts:
        alerts = sorted(alerts, key=lambda x: (x["score"], x["rvol"]), reverse=True)
        for row in alerts:
            send_telegram_message(format_alert(row))
    else:
        print("No alerts found.")

    if SEND_SUMMARY and (alerts or SEND_EMPTY_SUMMARY):
        send_telegram_message(format_summary(all_results))


if __name__ == "__main__":
    main()
