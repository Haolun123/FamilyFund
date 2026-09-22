#!/usr/bin/env python3
"""每日市场温度计推送入口。

由 cron 调用，非交易日自动跳过。
用法：
  python3 scripts/daily_push.py
  python3 scripts/daily_push.py --force   # 忽略交易日检查，强制推送（测试用）

健壮性设计：
  - 拉取总超时 10 分钟，超时后用上次缓存数据兜底推送
  - 缓存兜底时推送头部加 ⚠️ 提示，告知数据来自缓存
"""

import logging
import os
import sys
import signal

# 将 src/ 加入路径，兼容直接运行和 cron 调用
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

from market_monitor import get_market_data, _load_cache
from notifier import send_market_summary, _is_trading_day
from fundamentals import append_fundamentals_snapshot
from market_monitor import append_vol_snapshot

import argparse
from datetime import date

FETCH_TIMEOUT_SEC = 600  # 10 分钟拉取总超时


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout()


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

    _data_dir = os.environ.get('FAMILYFUND_DATA', os.path.expanduser('~/data'))

    # ── 拉取市场数据（带总超时保护）──
    market_data = None
    using_cache = False

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(FETCH_TIMEOUT_SEC)
    try:
        logging.info("拉取市场数据 (force_refresh=True)...")
        market_data = get_market_data(force_refresh=True)
        signal.alarm(0)  # 取消超时
        logging.info("市场数据拉取完成")
    except _Timeout:
        signal.alarm(0)
        logging.warning(f"拉取超时（>{FETCH_TIMEOUT_SEC}s），降级使用缓存数据")
        market_data = _load_cache()
        using_cache = True
    except Exception as e:
        signal.alarm(0)
        logging.warning(f"拉取异常: {e}，降级使用缓存数据")
        market_data = _load_cache()
        using_cache = True

    if not market_data:
        logging.error("无缓存数据，无法推送")
        sys.exit(1)

    # ── 基本面快照（非关键，失败不影响推送）──
    if not using_cache:
        try:
            stats = append_fundamentals_snapshot(_data_dir)
            logging.info(f"基本面快照已更新: {stats}")
        except Exception as e:
            logging.warning(f"基本面快照更新失败（不影响推送）: {e}")

        try:
            append_vol_snapshot(_data_dir, market_data)
            logging.info("QVIX 历史快照已更新")
        except Exception as e:
            logging.warning(f"QVIX 快照更新失败（不影响推送）: {e}")

    # ── 推送 ──
    logging.info("发送推送...")
    ok = send_market_summary(market_data, stale_cache=using_cache)

    if ok:
        logging.info("推送完成" + ("（缓存数据）" if using_cache else ""))
        sys.exit(0)
    else:
        logging.error("推送失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
