import os
import math
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import yfinance as yf


WIB = timezone(timedelta(hours=7))


def now_wib_str():
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except Exception:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(str(raw).strip()))
    except Exception:
        return default


def parse_tickers(raw: str) -> list[str]:
    if not raw:
        return []
    items = []
    for x in raw.split(","):
        t = x.strip().upper()
        if not t:
            continue
        if not t.endswith(".JK"):
            t = f"{t}.JK"
        items.append(t)
    # deduplicate while preserving order
    seen = set()
    out = []
    for t in items:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def short_ticker(ticker: str) -> str:
    return ticker.replace(".JK", "")


def send_telegram(text: str):
    token = env_str("TELEGRAM_BOT_TOKEN")
    chat_id = env_str("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram credentials missing.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    try:
        r = requests.post(url, data=payload, timeout=20)
        print("Telegram status:", r.status_code, r.text[:500])
    except Exception as e:
        print("Telegram send failed:", e)


def yf_download_one(ticker: str, interval: str, period: str = "5d") -> pd.DataFrame:
    try:
        df = yf.download(
            tickers=ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.title)
        needed = ["Open", "High", "Low", "Close", "Volume"]
        for c in needed:
            if c not in df.columns:
                return pd.DataFrame()
        df = df[needed].copy()
        df = df.dropna()
        return df
    except Exception as e:
        print(f"Download error {ticker}: {e}")
        return pd.DataFrame()


def calc_bar_value_idr(close_price: float, volume_shares: float) -> float:
    if close_price is None or volume_shares is None:
        return 0.0
    if math.isnan(close_price) or math.isnan(volume_shares):
        return 0.0
    return float(close_price) * float(volume_shares)


def analyze_one(
    ticker: str,
    group_name: str,
    interval: str,
    lookback_bars: int,
    min_rvol: float,
    min_price_change_pct: float,
    min_bar_value_idr: float,
) -> dict | None:
    df = yf_download_one(ticker, interval=interval, period="5d")
    if df.empty:
        return None

    if len(df) < max(lookback_bars + 2, 25):
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]
    hist = df.iloc[-(lookback_bars + 1):-1].copy()

    avg_vol = hist["Volume"].mean()
    if avg_vol is None or pd.isna(avg_vol) or avg_vol <= 0:
        rvol = 0.0
    else:
        rvol = float(last["Volume"]) / float(avg_vol)

    prev_close = float(prev["Close"]) if not pd.isna(prev["Close"]) else 0.0
    last_close = float(last["Close"]) if not pd.isna(last["Close"]) else 0.0
    if prev_close > 0:
        price_change_pct = ((last_close / prev_close) - 1.0) * 100.0
    else:
        price_change_pct = 0.0

    breakout_ref = hist["High"].max()
    breakout = bool(last_close > breakout_ref) if not pd.isna(breakout_ref) else False

    bar_value_idr = calc_bar_value_idr(last_close, float(last["Volume"]))

    score = 0
    if rvol >= min_rvol:
        score += 40
    elif rvol >= min_rvol * 0.8:
        score += 20

    if price_change_pct >= min_price_change_pct:
        score += 30
    elif price_change_pct >= min_price_change_pct * 0.6:
        score += 15

    if bar_value_idr >= min_bar_value_idr:
        score += 20
    elif bar_value_idr >= min_bar_value_idr * 0.6:
        score += 10

    if breakout:
        score += 10

    status = "ALERT" if (
        rvol >= min_rvol
        and price_change_pct >= min_price_change_pct
        and bar_value_idr >= min_bar_value_idr
    ) else "WATCH"

    return {
        "ticker": short_ticker(ticker),
        "group": group_name,
        "last_close": last_close,
        "price_change_pct": price_change_pct,
        "rvol": rvol,
        "bar_value_idr": bar_value_idr,
        "breakout": breakout,
        "score": score,
        "status": status,
    }


def format_idr(n: float) -> str:
    if n >= 1_000_000_000:
        return f"Rp{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"Rp{n/1_000_000:.0f}M"
    return f"Rp{n:,.0f}"


def build_line(r: dict) -> str:
    bo = "YES" if r["breakout"] else "NO"
    return (
        f"- {r['ticker']} | {r['status']} | "
        f"RVOL {r['rvol']:.2f}x | Δ {r['price_change_pct']:.2f}% | "
        f"Value {format_idr(r['bar_value_idr'])} | BO {bo} | Skor {r['score']}"
    )


def top_lines_by_group(results: list[dict], group_name: str, top_n: int) -> list[str]:
    rows = [r for r in results if r["group"] == group_name]
    rows = sorted(rows, key=lambda x: (x["score"], x["rvol"], x["price_change_pct"]), reverse=True)
    if not rows:
        return ["- Tidak ada data"]
    return [build_line(r) for r in rows[:top_n]]


def format_summary(all_results: list[dict], total_tickers: int, top_n_per_group: int) -> str:
    alerts = [r for r in all_results if r["status"] == "ALERT"]

    parts = [
        "📊 Scan selesai",
        f"Waktu: {now_wib_str()}",
        f"Jumlah ticker: {total_tickers}",
        f"Jumlah alert: {len(alerts)}",
        "",
        "LARGE",
        *top_lines_by_group(all_results, "LARGE", top_n_per_group),
        "",
        "MID",
        *top_lines_by_group(all_results, "MID", top_n_per_group),
        "",
        "SMALL",
        *top_lines_by_group(all_results, "SMALL", top_n_per_group),
    ]
    return "\n".join(parts)


def format_alerts(alert_rows: list[dict]) -> list[str]:
    if not alert_rows:
        return []

    rows = sorted(
        alert_rows,
        key=lambda x: (x["group"], x["score"], x["rvol"], x["price_change_pct"]),
        reverse=True,
    )

    messages = []
    for r in rows:
        msg = "\n".join([
            "🚨 INDO STOCK ALERT",
            f"{r['ticker']} | {r['group']}",
            f"Waktu: {now_wib_str()}",
            f"Harga: {r['last_close']:.2f}",
            f"Perubahan: {r['price_change_pct']:.2f}%",
            f"RVOL: {r['rvol']:.2f}x",
            f"Nilai bar: {format_idr(r['bar_value_idr'])}",
            f"Breakout: {'YES' if r['breakout'] else 'NO'}",
            f"Skor: {r['score']}",
        ])
        messages.append(msg)
    return messages


def main():
    large_tickers = parse_tickers(env_str("LARGE_TICKERS"))
    mid_tickers = parse_tickers(env_str("MID_TICKERS"))
    small_tickers = parse_tickers(env_str("SMALL_TICKERS"))

    lookback_bars = env_int("LOOKBACK_BARS", 20)
    bar_interval = env_str("BAR_INTERVAL", "1m")
    top_n_per_group = env_int("TOP_N_PER_GROUP", 3)
    send_summary = env_str("SEND_SUMMARY", "0") == "1"

    large_cfg = {
        "min_rvol": env_float("LARGE_MIN_RVOL", 1.8),
        "min_price_change_pct": env_float("LARGE_MIN_PRICE_CHANGE_PCT", 0.30),
        "min_bar_value_idr": env_float("LARGE_MIN_BAR_VALUE_IDR", 2_000_000_000),
    }
    mid_cfg = {
        "min_rvol": env_float("MID_MIN_RVOL", 2.2),
        "min_price_change_pct": env_float("MID_MIN_PRICE_CHANGE_PCT", 0.50),
        "min_bar_value_idr": env_float("MID_MIN_BAR_VALUE_IDR", 750_000_000),
    }
    small_cfg = {
        "min_rvol": env_float("SMALL_MIN_RVOL", 3.0),
        "min_price_change_pct": env_float("SMALL_MIN_PRICE_CHANGE_PCT", 0.80),
        "min_bar_value_idr": env_float("SMALL_MIN_BAR_VALUE_IDR", 300_000_000),
    }

    print("=== DEBUG WATCHLIST ===")
    print("LARGE_TICKERS:", large_tickers)
    print("MID_TICKERS:", mid_tickers)
    print("SMALL_TICKERS:", small_tickers)
    print("BAR_INTERVAL:", bar_interval)
    print("LOOKBACK_BARS:", lookback_bars)
    print("SEND_SUMMARY:", send_summary)

    all_results: list[dict] = []

    for t in large_tickers:
        r = analyze_one(
            ticker=t,
            group_name="LARGE",
            interval=bar_interval,
            lookback_bars=lookback_bars,
            min_rvol=large_cfg["min_rvol"],
            min_price_change_pct=large_cfg["min_price_change_pct"],
            min_bar_value_idr=large_cfg["min_bar_value_idr"],
        )
        if r:
            all_results.append(r)

    for t in mid_tickers:
        r = analyze_one(
            ticker=t,
            group_name="MID",
            interval=bar_interval,
            lookback_bars=lookback_bars,
            min_rvol=mid_cfg["min_rvol"],
            min_price_change_pct=mid_cfg["min_price_change_pct"],
            min_bar_value_idr=mid_cfg["min_bar_value_idr"],
        )
        if r:
            all_results.append(r)

    for t in small_tickers:
        r = analyze_one(
            ticker=t,
            group_name="SMALL",
            interval=bar_interval,
            lookback_bars=lookback_bars,
            min_rvol=small_cfg["min_rvol"],
            min_price_change_pct=small_cfg["min_price_change_pct"],
            min_bar_value_idr=small_cfg["min_bar_value_idr"],
        )
        if r:
            all_results.append(r)

    total_tickers = len(large_tickers) + len(mid_tickers) + len(small_tickers)
    alert_rows = [r for r in all_results if r["status"] == "ALERT"]

    print("=== DEBUG RESULT COUNT ===")
    print("total_tickers:", total_tickers)
    print("rows_analyzed:", len(all_results))
    print("alerts:", len(alert_rows))

    if send_summary:
        summary = format_summary(all_results, total_tickers, top_n_per_group)
        print(summary)
        send_telegram(summary)
    else:
        messages = format_alerts(alert_rows)
        if not messages:
            print("No alerts to send.")
        for msg in messages:
            print(msg)
            send_telegram(msg)


if __name__ == "__main__":
    main()
