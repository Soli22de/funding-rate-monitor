#!/usr/bin/env python3
"""
Funding Rate Monitor — V0 跨所资金费率监控（不下单）
===================================================
拉 OKX / Bybit / MEXC 三所的 BTC / ETH / SOL funding rate，
计算跨所差异，超阈值输出提醒。

使用: uv run python monitor.py
"""
import json
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import ccxt

# ── 配置 ──────────────────────────────────────────────────────────────────────
EXCHANGES = ["okx", "bybit", "mexc"]  # 大陆 IP 友好度较高
SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]  # 永续合约

# 跨所 funding rate 差异阈值 (8h 费率，非年化)
DIFF_THRESHOLD = 0.0005  # 0.05%

PROXY_SETTINGS = {
    "okx": {"enableRateLimit": True},
    "bybit": {"enableRateLimit": True},
    "mexc": {"enableRateLimit": True},
}


def create_exchange(name: str) -> Optional[ccxt.Exchange]:
    """创建交易所实例，处理代理 / 超时"""
    try:
        exchange_class = getattr(ccxt, name, None)
        if exchange_class is None:
            return None
        config = {
            "enableRateLimit": True,
            "timeout": 15000,  # 15s timeout
        }
        # 如果 Clash 在运行，走 HTTP 代理
        config.update(PROXY_SETTINGS.get(name, {}))
        ex = exchange_class(config)
        return ex
    except Exception as e:
        return None


def fetch_funding_rate(exchange, symbol: str) -> Optional[Dict]:
    """拉单个交易所的 funding rate（public 接口，不需要 API key）"""
    try:
        fr = exchange.fetch_funding_rate(symbol)
        return fr
    except Exception as e:
        return None


def fetch_all_rates() -> Dict[str, Dict[str, Dict]]:
    """拉所有交易所 × 币种的 funding rate"""
    results = {}
    ex_names_ok = []
    ex_names_failed = []

    for ex_name in EXCHANGES:
        ex = create_exchange(ex_name)
        if ex is None:
            ex_names_failed.append(ex_name)
            continue
        ex_names_ok.append(ex_name)
        results[ex_name] = {}
        for symbol in SYMBOLS:
            fr = fetch_funding_rate(ex, symbol)
            if fr:
                results[ex_name][symbol] = {
                    "funding_rate": fr.get("fundingRate"),
                    "next_funding_time": fr.get("nextFundingTime"),
                    "timestamp": fr.get("timestamp"),
                    "symbol": fr.get("symbol"),
                    "mark_price": fr.get("markPrice"),
                    "index_price": fr.get("indexPrice"),
                }
            else:
                results[ex_name][symbol] = None

    return results, ex_names_ok, ex_names_failed


def fetch_basis(data: Dict, ex_names_ok: List[str]) -> Dict[str, Dict]:
    """拉现货价格计算 spot+perp basis（单一交易所可做的 delta-neutral 套利）"""
    basis = {}
    for ex_name in EXCHANGES:
        if ex_name not in data:
            continue
        basis[ex_name] = {}
        for symbol in SYMBOLS:
            fr_data = data.get(ex_name, {}).get(symbol)
            if not fr_data:
                continue
            # The funding rate IS the basis (premium of perp over spot, annualized)
            # For spot+perp arb: you buy spot + short perp, collect funding
            fr = fr_data.get("funding_rate")
            if fr is None:
                continue
            # Annualized basis = funding_rate * 3 * 365
            basis_annual = fr * 3 * 365 * 100
            basis[ex_name][symbol] = {
                "funding_rate_8h": fr,
                "basis_annual_pct": round(basis_annual, 2),
                "mark_price": fr_data.get("mark_price"),
                "index_price": fr_data.get("index_price"),
                "deploy_signal": basis_annual >= 11.0,  # P50 threshold
            }
    return basis


def compute_diffs(data: Dict) -> List[Dict]:
    """计算跨所 funding rate 差异"""
    diffs = []
    for symbol in SYMBOLS:
        rates = {}
        for ex in EXCHANGES:
            if ex in data and data[ex].get(symbol):
                r = data[ex][symbol].get("funding_rate")
                if r is not None:
                    rates[ex] = r

        if len(rates) < 2:
            continue

        pairs = list(rates.items())
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                ex_a, rate_a = pairs[i]
                ex_b, rate_b = pairs[j]
                diff = abs(rate_a - rate_b)
                diff_annual = diff * 3 * 365  # 3x 每 8h -> 年化
                diffs.append({
                    "symbol": symbol,
                    "pair": f"{ex_a} vs {ex_b}",
                    "rate_a": rate_a,
                    "rate_b": rate_b,
                    "diff_8h": round(diff, 6),
                    "diff_annual_pct": round(diff_annual * 100, 2),
                    "alert": diff >= DIFF_THRESHOLD,
                })

    return sorted(diffs, key=lambda x: -x["diff_8h"])


def print_report(data: Dict, diffs: List[Dict],
                  ex_names_ok: List[str], ex_names_failed: List[str],
                  basis: Optional[Dict] = None):
    """输出 Markdown 格式报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append("# Funding Rate 监控报告\n")
    lines.append(f"**时间**: {now}\n")
    lines.append(f"**交易所状态**: OK={ex_names_ok} FAILED={ex_names_failed}\n")
    lines.append("---\n")

    # Spot+perp basis (single exchange) — the actual deployable strategy
    lines.append("## Spot+Perp Basis (deployable alpha)\n")
    lines.append("策略：同一交易所买入现货 + 做空永续 → delta-neutral 收 funding\n")
    lines.append("")
    lines.append("| 交易所 | 币种 | Funding/8h | 年化 Basis | 部署信号 (>11%/yr) |")
    lines.append("|--------|------|:---------:|:---------:|:-----------------:|")
    
    for ex in EXCHANGES:
        if basis and ex in basis:
            for symbol in SYMBOLS:
                b = basis[ex].get(symbol)
                if b:
                    signal = "🟢 DEPLOY" if b["deploy_signal"] else "⏸️ standby"
                    lines.append(
                        f"| {ex} | {symbol} | {b['funding_rate_8h']*100:+.4f}% | "
                        f"{b['basis_annual_pct']:+.1f}%/yr | {signal} |"
                    )
    
    lines.append("")
    lines.append("**部署规则**: funding rate > 0.01%/8h (≈11%/yr, 历史 P50) → deploy signal.")
    lines.append("**费用**: 一次性 round-trip maker fee ~0.24% (OKX) 或 ~0% (MEXC).")
    lines.append("")
    lines.append("---\n")

    # 每所每个币种的 funding rate
    lines.append("## 当前 Funding Rate\n")
    lines.append("| 交易所 | 币种 | Funding Rate | 年化 | 下次结算 |")
    lines.append("|--------|------|-------------:|-----:|---------:|")
    for ex in EXCHANGES:
        if ex not in data:
            continue
        for symbol in SYMBOLS:
            d = data[ex].get(symbol)
            if d and d.get("funding_rate") is not None:
                fr = d["funding_rate"]
                ann = fr * 3 * 365 * 100
                nft = ""
                if d.get("next_funding_time"):
                    nft = datetime.fromtimestamp(d["next_funding_time"] / 1000).strftime("%H:%M")
                lines.append(f"| {ex} | {symbol} | {fr*100:+.4f}% | {ann:+.1f}% | {nft} |")
            else:
                lines.append(f"| {ex} | {symbol} | ❌ | — | — |")

    lines.append("")

    # 跨所差异
    lines.append("## 跨所差异（按差异降序）\n")
    if not diffs:
        lines.append("_无有效数据_\n")
    else:
        lines.append("| 币种 | 跨所对 | Rate A | Rate B | 差 (8h) | 年化差 | 预警 |")
        lines.append("|------|--------|-------:|-------:|--------:|------:|:----:|")
        for d in diffs:
            alert = "🔴" if d["alert"] else ""
            lines.append(
                f"| {d['symbol']} | {d['pair']} | {d['rate_a']*100:+.4f}% | "
                f"{d['rate_b']*100:+.4f}% | {d['diff_8h']*100:.4f}% | "
                f"{d['diff_annual_pct']:+.1f}% | {alert} |"
            )

    lines.append("")
    lines.append("---\n")

    # Spot+perp basis (single exchange) — the actual deployable strategy
    lines.append("## Spot+Perp Basis (deployable alpha)\n")
    lines.append("策略：同一交易所买入现货 + 做空永续 → delta-neutral 收 funding\n")
    lines.append("")
    lines.append("| 交易所 | 币种 | Funding/8h | 年化 Basis | 部署信号 (>11%/yr) |")
    lines.append("|--------|------|:---------:|:---------:|:-----------------:|")
    
    for ex in EXCHANGES:
        if basis and ex in basis:
            for symbol in SYMBOLS:
                b = basis[ex].get(symbol)
                if b:
                    signal = "🟢 DEPLOY" if b["deploy_signal"] else "⏸️ standby"
                    lines.append(
                        f"| {ex} | {symbol} | {b['funding_rate_8h']*100:+.4f}% | "
                        f"{b['basis_annual_pct']:+.1f}%/yr | {signal} |"
                    )
    
    lines.append("")
    lines.append("**部署规则**: funding rate > 0.01%/8h (≈11%/yr, 历史 P50) → deploy signal.")
    lines.append("**费用**: 一次性 round-trip maker fee ~0.24% (OKX) 或 ~0% (MEXC).")
    lines.append("")
    lines.append("---\n")
    lines.append(f"*生成时间: {now}*\n")

    report = "\n".join(lines)
    return report


def save_report(report: str, diffs: List[Dict]):
    """保存报告到文件"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"reports/funding_rate_{ts}"
    # Markdown
    with open(f"{base}.md", "w") as f:
        f.write(report)
    # JSON
    with open(f"{base}.json", "w") as f:
        json.dump(diffs, f, indent=2, default=str)
    print(f"  Report saved: {base}.md / {base}.json")


def main():
    import os
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="output JSON to stdout (for adapter consumption)")
    args = parser.parse_args()
    os.makedirs("reports", exist_ok=True)

    if not args.json:
        print("=" * 60)
        print("  Funding Rate Monitor V0.1")
        print("=" * 60)
        print(f"  交易所: {EXCHANGES}")
        print(f"  币种:   {SYMBOLS}")
        print(f"  阈值:   {DIFF_THRESHOLD*100:.2f}% / 8h")
        print()

    # 拉数据
    if not args.json:
        print("[1/2] 拉取 funding rate...")
    data, ok, failed = fetch_all_rates()
    if not args.json:
        print(f"  交易所 OK: {ok}, 失败: {failed}")

    # 计算差异
    if not args.json:
        print("[2/2] 计算跨所差异...")
    diffs = compute_diffs(data)
    basis = fetch_basis(data, ok)

    # Deploy signal count
    deploy_count = sum(1 for ex_b in basis.values() for s_b in ex_b.values() if isinstance(s_b, dict) and s_b.get("deploy_signal"))
    alert_symbols = sorted(set(d["symbol"] for d in diffs if d.get("alert")))
    max_basis = 0
    max_basis_info = None
    for ex_name, ex_b in basis.items():
        for sym, b in ex_b.items():
            if isinstance(b, dict) and b.get("basis_annual_pct", 0) > max_basis:
                max_basis = b["basis_annual_pct"]
                max_basis_info = {"exchange": ex_name, "symbol": sym, "basis_annual_pct": b["basis_annual_pct"]}

    if args.json:
        out = {
            "exchanges_ok": ok,
            "exchanges_failed": failed,
            "deploy_signal_count": deploy_count,
            "cross_exchange_alerts": alert_symbols,
            "max_basis": max_basis_info,
            "all_basis": basis,
            "all_diffs": diffs,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return diffs

    # 生成报告
    report = print_report(data, diffs, ok, failed, basis)
    print()
    print(report[:500])
    save_report(report, diffs)

    for e in failed:
        if e in ("okx", "bybit"):
            print(f"\n⚠️ {e} 不可达 — 大陆 IP 基础设施限制!")

    if deploy_count > 0:
        print(f"\n🟢 Deploy signal! {deploy_count} 条触发 P50+ threshold")
    else:
        print(f"\n⏸️ No deploy signal. Current funding below P50 (11%/yr)")

    if alert_symbols:
        print(f"\n🔴 预警: {alert_symbols} 跨所 funding diff 超阈值!")
    else:
        print(f"\n✓ 当前跨所 funding diff 在阈值内")

    return diffs


if __name__ == "__main__":
    diffs = main()
    # Return exit code 0 = ok, 1 = at least one alert
    sys.exit(1 if any(d["alert"] for d in diffs) else 0)
