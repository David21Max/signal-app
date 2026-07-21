#!/usr/bin/env python3
# Signal-Wächter: prüft BTC & ETH auf mehreren Zeitfenstern
# und schickt eine ntfy-Push-Nachricht, wenn ein Signal kippt.
# Läuft automatisch via GitHub Actions (siehe .github/workflows/signals.yml)

import json, os, urllib.request

# ================== EINSTELLUNGEN ==================
NTFY_TOPIC = "kursradar-david21max-x4t7q9"   # dein privater ntfy-Kanal
COINS = ["BTCUSDT", "ETHUSDT", "PAXGUST"]
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
HOSTS = [
    "https://data-api.binance.vision/api/v3",
    "https://api.binance.com/api/v3",
]
STATE_FILE = "state.json"

# ================== DATEN ==================
def fetch_candles(sym, tf):
    for host in HOSTS:
        try:
            url = f"{host}/klines?symbol={sym}&interval={tf}&limit=400"
            with urllib.request.urlopen(url, timeout=20) as r:
                raw = json.load(r)
            return [
                {"o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
                 "c": float(k[4]), "v": float(k[5])}
                for k in raw
            ]
        except Exception:
            continue
    return None

# ================== INDIKATOREN ==================
def ema(a, n):
    k = 2 / (n + 1)
    out = [a[0]]
    for x in a[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out

def sma(a, n):
    out, s = [None] * len(a), 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out

def stdev(a, n):
    out = [None] * len(a)
    for i in range(n - 1, len(a)):
        seg = a[i - n + 1: i + 1]
        m = sum(seg) / n
        out[i] = (sum((x - m) ** 2 for x in seg) / n) ** 0.5
    return out

def rsi(a, n):
    out = [50.0] * len(a)
    if len(a) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = a[i] - a[i - 1]
        g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    out[n] = 100 - 100 / (1 + (100 if l == 0 else g / l))
    for i in range(n + 1, len(a)):
        d = a[i] - a[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + (100 if l == 0 else g / l))
    return out

def atr(candles, n=14):
    trs = []
    for i in range(1, len(candles)):
        h, lo, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a

# ================== SCORE (identisch zur App) ==================
def score(candles):
    c = [k["c"] for k in candles]
    o = [k["o"] for k in candles]
    v = [k["v"] for k in candles]
    i = len(c) - 1
    eF, eM, eS = ema(c, 20), ema(c, 50), ema(c, 200)
    r = rsi(c, 14)
    mF, mS = ema(c, 12), ema(c, 26)
    macd = [mF[j] - mS[j] for j in range(len(c))]
    sig = ema(macd, 9)
    bb, bd = sma(c, 20), stdev(c, 20)
    va = sma(v, 20)

    t = (1 if c[i] > eF[i] else -1) + (1 if eF[i] > eM[i] else -1) + (1 if eM[i] > eS[i] else -1)
    m = (1 if r[i] > 50 else -1) + (1 if macd[i] > sig[i] else -1) + (1 if macd[i] > macd[i - 1] else -1)
    vs = 0
    vavg = va[i] if va[i] else v[i]
    if v[i] > vavg * 1.2:
        vs = 1 if c[i] > o[i] else -1
    dev = (bd[i] or 1) * 2 or 1
    vo = 1 if (c[i] - (bb[i] or c[i])) / dev > 0 else -1

    raw = (t / 3 * 40) + (m / 3 * 30) + (vs * 15) + (vo * 15)
    # Squeeze-Dämpfung
    bw_now = (dev * 2) / (bb[i] or c[i])
    bws = [(bd[j] * 4) / bb[j] for j in range(max(20, i - 100), i + 1) if bb[j] and bd[j]]
    if len(bws) > 10 and bw_now < (sum(bws) / len(bws)) * 0.7:
        raw *= 0.6
    return max(-100, min(100, raw))

def state_of(s):
    return "LONG" if s >= 40 else "SHORT" if s <= -40 else "WARTEN"

# ================== PUSH ==================
def push(title, msg, tags):
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=msg.encode("utf-8"),
        headers={"Title": title, "Tags": tags, "Priority": "high"},
    )
    urllib.request.urlopen(req, timeout=20)

# ================== HAUPTLOGIK ==================
def main():
    old = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            old = json.load(f)
    new = {}

    for sym in COINS:
        for tf in TIMEFRAMES:
            candles = fetch_candles(sym, tf)
            if not candles or len(candles) < 250:
                continue
            s = score(candles)
            st = state_of(s)
            key = f"{sym}_{tf}"
            new[key] = st
            prev = old.get(key)
            if prev and prev != st and st != "WARTEN":
                price = candles[-1]["c"]
                a = atr(candles)
                if st == "LONG":
                    sl, tp = price - 1.5 * a, price + 3 * a
                else:
                    sl, tp = price + 1.5 * a, price - 3 * a
                coin = sym.replace("USDT", "")
                pct = round((s + 100) / 2)
                push(
                    f"{coin} {tf}: {prev} -> {st}",
                    f"Kurs: ${price:,.2f}\nScore: {pct}% {'long' if st=='LONG' else 'short'}\n"
                    f"Stop-Loss: ${sl:,.2f}\nZiel (2:1): ${tp:,.2f}",
                    "chart_with_upwards_trend" if st == "LONG" else "chart_with_downwards_trend",
                )

    with open(STATE_FILE, "w") as f:
        json.dump(new, f, indent=1)

if __name__ == "__main__":
    main()
