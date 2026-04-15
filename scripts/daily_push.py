#!/usr/bin/env python3
"""每日市场温度计推送入口。

由 cron 调用，非交易日自动跳过。
用法：
  python3 scripts/daily_push.py
  python3 scripts/daily_push.py --force   # 忽略交易日检查，强制推送（测试用）
"""

import logging
import os
import sys

# 将 src/ 加入路径，兼容直接运行和 cron 调用
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

from market_monitor import get_market_data
from notifier import send_market_summary, _is_trading_day, _send_webhook

import argparse
from datetime import date


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='强制推送，忽略交易日检查')
    args = parser.parse_args()

    today = date.today()
    logging.info(f"daily_push 启动: {today}, force={args.force}")

    # 交易日检查（--force 时跳过）
    if not args.force and not _is_trading_day(today):
        logging.info(f"{today} 非交易日，退出")
        sys.exit(0)

    logging.info("拉取市场数据 (force_refresh=True)...")
    market_data = get_market_data(force_refresh=True)

    logging.info("发送推送...")
    ok = send_market_summary(market_data)

    if ok:
        logging.info("推送完成")
        sys.exit(0)
    else:
        logging.error("推送失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
