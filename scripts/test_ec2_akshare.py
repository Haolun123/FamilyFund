#!/usr/bin/env python3
"""EC2 akshare 可达性测试脚本。在 EC2 上直接运行：python3 test_ec2_akshare.py"""

import time

try:
    import akshare as ak
    print(f"akshare version: {ak.__version__}\n")
except ImportError:
    print("akshare not installed. Run: pip3 install akshare")
    exit(1)

tests = [
    ("CSI300 日线价格",  lambda: ak.stock_zh_index_daily(symbol="sh000300").tail(1)),
    ("中证500 PE",      lambda: ak.stock_index_pe_lg(symbol="中证500").tail(1)),
    ("沪深300 PE",      lambda: ak.stock_index_pe_lg(symbol="沪深300").tail(1)),
    ("QVIX",           lambda: ak.index_option_300etf_qvix().tail(1)),
    ("A股交易日历",      lambda: ak.tool_trade_date_hist_sina().tail(1)),
]

passed = 0
failed = 0

for name, fn in tests:
    t0 = time.time()
    try:
        result = fn()
        elapsed = time.time() - t0
        print(f"✓ {name}: {elapsed:.1f}s")
        print(f"  {result.to_string(index=False)}")
        passed += 1
    except Exception as e:
        elapsed = time.time() - t0
        print(f"✗ {name}: {elapsed:.1f}s")
        print(f"  ERROR: {e}")
        failed += 1
    print()

print(f"结果: {passed} 通过 / {failed} 失败")
if failed > 0:
    print("提示: 失败的接口可能被海外IP限制，考虑配置HTTP代理或迁移到国内云。")
