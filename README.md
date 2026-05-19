# funding_rate_monitor — 加密永续合约资金费率监控（不下单）

跨所 funding rate 差异监控工具。V0 只拉数据不交易。

## 目标

- 用 ccxt 拉 OKX / Bybit / MEXC 三所的 BTC / ETH / SOL 实时 funding rate
- 计算跨所差异
- 超阈值提醒（> 0.05% / 8h）
- 输出 Markdown 报告 + JSON

## 依赖

Python 3.11+（uv 管理），详见 pyproject.toml。

## 使用

```bash
cd ~/jz_code/funding_rate_monitor
uv sync
uv run python monitor.py
```

## 状态

V0 — 监控不下单（2026-05-19）

---

*Part of the [quant-research-log](https://github.com/Soli22de/quant-research-log) project family. 协作指南: [COLLABORATION.md](https://github.com/Soli22de/quant-research-log/blob/main/COLLABORATION.md)*
