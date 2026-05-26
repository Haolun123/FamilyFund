# DESIGN: 市场温度计每日推送（Daily Push）

**状态**: 待 Review  
**日期**: 2026-04-15  
**作者**: Haolun Yan

---

## 一、目标

每个交易日早上 8:30（北京时间）自动推送一条企业微信消息，内容为市场温度计的核心信号。无需主动打开 Dashboard，开盘前一眼完成决策。

---

## 二、推送内容设计

企业微信群机器人支持 Markdown，消息示例如下：

```
📊 **市场温度计** 2026-04-15

**乖离率**
| 标的 | 当前价 | 主要信号 |
|------|--------|---------|
| CSI300 | 4,701 | ⚪ 正常 (MA60 +2.1%) |
| 中证A500 | 5,821 | ⚪ 正常 (MA60 +1.8%) |
| 黄金 | 3,285 | 🟡 偏高 (MA200 +12.3%) |
| 标普500 | 5,180 | 🟢 超卖 (MA200 -5.5%) |
| 纳指100 | 18,200 | 🟢 超卖 (MA200 -4.8%) |

**恐慌指数**
VIX 17.7 ⚪ 正常波动　｜　QVIX 17.7 ⚪ 正常波动

**定投倍数建议**
标普500　PE 27.2 × VIX 17.7 → **0.5x**
纳指100　PE 32.6 × VIX 17.7 → **0.3x**
CSI300　　PE 13.7 × QVIX 17.7 → **观望**
中证A500　PE 28.5 × QVIX 17.7 → **观望** ⚠️PE代理:中证500

> 数据为前一交易日收盘，仅供参考，不构成投资建议。
```

### 消息规则

- **非交易日不推送**：周六日跳过；中国大陆节假日跳过（A股休市）
- **数据失败降级**：某个标的拉取失败时，该行显示 `⚠️ 数据不可用`，其余正常推送
- **全部失败**：发送一条简短失败通知而非静默

---

## 三、技术方案

### 3.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/notifier.py` | 推送逻辑：格式化消息 + 调用企业微信 Webhook |
| `scripts/daily_push.py` | 入口脚本：判断交易日 → 拉取数据 → 推送 |
| `deploy/ec2_setup.sh` | EC2 一键初始化脚本（venv + 依赖 + cron）|

不改动任何现有文件。不使用 Docker（无隔离需求，直接 venv 更简单）。

### 3.2 推送入口脚本 `scripts/daily_push.py`

```python
#!/usr/bin/env python3
"""每日市场温度计推送入口。由 cron 调用，非交易日自动跳过。"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from market_monitor import get_market_data
from notifier import send_market_summary

def main():
    data = get_market_data(force_refresh=True)
    send_market_summary(data)

if __name__ == '__main__':
    main()
```

### 3.3 `src/notifier.py` 核心结构

```python
def send_market_summary(market_data: dict) -> bool:
    """判断是否交易日，格式化并发送企业微信消息。返回是否成功。"""

def _format_message(market_data: dict) -> str:
    """将 market_data 渲染为企业微信 Markdown 字符串。"""

def _send_webhook(text: str) -> bool:
    """POST 到企业微信群机器人 Webhook，带重试（最多3次，间隔5s）。"""

def _is_trading_day() -> bool:
    """判断今天是否为交易日（排除周末 + 中国大陆法定节假日）。
    使用 akshare.tool_trade_date_hist_sina() 获取完整交易日历。"""
```

### 3.4 企业微信 Webhook 配置

Webhook URL 通过环境变量注入，不硬编码，不提交到 git：

```bash
WXWORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxx
```

市场温度计只拉公开市场数据（akshare/yfinance），与个人持仓数据完全解耦，无需设置 `FAMILYFUND_DATA`。`market_cache.json` 会自动写入项目 `data/` 目录作为日间缓存。

**PE 历史快照**（2026-05-08 新增）：`daily_push.py` 同时会将美股/ADR 标的的当日 `trailingPE` 追加到 `~/data/pe_history_us.json`，用于积累历史 PE 分位数据。需要提前创建 `~/data/` 目录（`mkdir -p ~/data`）。

---

## 四、EC2 部署方案

### 4.1 实例选型

| 参数 | 选择 |
|------|------|
| 实例类型 | **t3.micro**（1 vCPU，1GB RAM）|
| Region | ap-southeast-2（已确认 akshare 可访问）|
| 系统 | Amazon Linux 2023 |
| 计费方式 | On-Demand ~$8/月 |
| 存储 | 8GB gp3（默认）|
| 运行方式 | 直接 Python venv + cron，**不使用 Docker** |

> akshare 在 ap-southeast-2 已实测可访问，风险消除。

### 4.2 目录结构

```
/home/ec2-user/
├── familyfund/          # git clone 整个项目
│   ├── src/
│   ├── scripts/
│   └── ...
├── familyfund-venv/     # Python 虚拟环境
├── familyfund.env       # Webhook URL（不在 git 中）
├── data/                # 缓存目录
│   ├── market_cache.json       # 市场数据日间缓存
│   └── pe_history_us.json      # 美股/ADR 历史PE快照（每日追加）
└── logs/
    └── push.log
```

> **初次部署需手动创建 `~/data/` 目录**：`mkdir -p ~/data`

### 4.3 Cron 配置

EC2 默认时区 UTC，北京时间 8:30 = UTC 00:30：

```cron
30 0 * * * source /home/ec2-user/familyfund.env && /home/ec2-user/familyfund-venv/bin/python /home/ec2-user/familyfund/scripts/daily_push.py >> /home/ec2-user/logs/push.log 2>&1
```

### 4.4 `deploy/ec2_setup.sh`

```bash
#!/bin/bash
# EC2 Amazon Linux 2023 一键初始化
set -e

BASE=/home/ec2-user

# 1. 安装系统依赖
sudo dnf install -y python3 python3-pip git

# 2. Clone 项目
git clone https://github.com/Haolun123/FamilyFund.git $BASE/familyfund

# 3. 创建 venv 并安装依赖（仅推送所需）
python3 -m venv $BASE/familyfund-venv
$BASE/familyfund-venv/bin/pip install --quiet akshare yfinance pandas requests

# 4. 创建日志和数据目录
mkdir -p $BASE/logs
mkdir -p $BASE/data

# 5. 创建 .env 文件（需手动填入 Webhook URL）
cat > $BASE/familyfund.env << 'ENVEOF'
export WXWORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY_HERE
ENVEOF
chmod 600 $BASE/familyfund.env

# 6. 配置 cron（UTC 00:30 = 北京时间 08:30）
CRON_LINE="30 0 * * * source $BASE/familyfund.env && $BASE/familyfund-venv/bin/python $BASE/familyfund/scripts/daily_push.py >> $BASE/logs/push.log 2>&1"
(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -

echo ""
echo "Setup complete."
echo ">>> 请编辑 $BASE/familyfund.env，填入真实的 WXWORK_WEBHOOK_URL 和 FAMILYFUND_DATA <<<"
echo "手动测试: source $BASE/familyfund.env && $BASE/familyfund-venv/bin/python $BASE/familyfund/scripts/daily_push.py --force"
```

`familyfund.env` 需要包含：
```bash
export WXWORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
export FAMILYFUND_DATA=/home/ec2-user/data
```

> **重要**：`FAMILYFUND_DATA` 必须设置，否则 `market_cache.json` 会写到代码目录的 `data/` 子目录，无法被 scp 正确找到。

---

## 五、market_cache.json 作为离线备份

### 功能说明

`daily_push.py` 调用 `get_market_data(force_refresh=True)` 时，会将所有市场数据写入 `$FAMILYFUND_DATA/market_cache.json`。该文件格式与 Dashboard 直接读取的缓存格式**完全一致**，可无缝替换使用。

### 包含的数据

| 字段 | 说明 |
|------|------|
| `vix` | VIX 标普500波动率 |
| `vxn` | VXN 纳指100波动率（来源：CBOE）|
| `qvix` | A股隐含波动率 |
| `pe_sp500` / `pe_ndx100` | 美股实时 PE |
| `pe_csi300` / `pe_csi_a500` | A股 PE |
| `csi300` / `csi_a500` / `sp500` / `ndx100` / `gold` | 各标的价格 + MA60/MA200 |
| `treasury_10y` | 美债10年期收益率 |

### 使用场景

当公司网络屏蔽 yfinance/akshare 等数据源时，从 EC2 scp 一份最新缓存即可恢复 Dashboard 正常显示：

```bash
scp ec2-user@<EC2_IP>:~/data/market_cache.json \
    ~/Library/Mobile\ Documents/com~apple~CloudDocs/Project_shared_files/FamilyFund/data/market_cache.json
```

文件放入后 Dashboard 直接读取，无需任何额外操作。缓存有效期为当日，次日自动失效（`_updated` 字段控制）。

### 功能说明

`daily_push.py` 每次运行时，会额外将 `yf_symbols.json` 中所有美股/ADR 标的（非 `.SS`/`.SZ`/`.HK`）的当日 `trailingPE` 追加到 `~/data/pe_history_us.json`（幂等，同一天不重复写入）。

数据格式：
```json
{
  "SAP": [
    {"date": "2026-05-08", "pe": 23.13},
    {"date": "2026-05-09", "pe": 22.87},
    ...
  ]
}
```

### 如何使用分位数据

积累足够历史（建议至少10条，理想1年以上）后：

```bash
# 从 EC2 拉取到本地 iCloud 数据目录
scp ec2-user@<EC2_IP>:~/data/pe_history_us.json \
    ~/Library/Mobile\ Documents/com~apple~CloudDocs/Project_shared_files/FamilyFund/data/
```

文件放入后，Dashboard 的「个股基本面」面板会自动显示 PE 历史分位数（区间、中位数、当前分位）。

### 注意事项

- A股（`.SS`/`.SZ`）和港股（`.HK`）的历史 PE 通过 akshare 实时拉取，不依赖此文件
- SAP 等美股无免费历史 PE API，只能靠每日快照积累
- EC2 需提前创建 `~/data/` 目录：`mkdir -p ~/data`

## 六、PE 历史快照（美股/ADR）

`daily_push.py` 每次运行时，会额外将 `yf_symbols.json` 中所有美股/ADR 标的（非 `.SS`/`.SZ`/`.HK`）的当日 `trailingPE` 追加到 `$FAMILYFUND_DATA/pe_history_us.json`（幂等，同一天不重复写入）。

详见文档开头的 PE 历史快照说明。

## 七、QVIX 历史快照与动态分位

### 功能说明

`daily_push.py` 同时将当日 QVIX 追加到 `$FAMILYFUND_DATA/vol_history.json`，用于计算 QVIX 动态历史分位数。

**为什么只做 QVIX，不做 VIX/VXN：**
VIX/VXN 的分级阈值（<15/15-20/20-30/>30）已基于几十年历史数据标定，固定分级就够用。QVIX 的阈值是硬编码的2015年以来历史均值，随市场结构变化会失真，动态分位更精准。

```json
{
  "qvix": [
    {"date": "2026-05-09", "value": 16.27},
    ...
  ]
}
```

### Dashboard 展示

积累10条以上后，QVIX 卡片下方自动显示：
```
近XXX天分位: 35.2%   区间 12.5–28.3
```
- 🔴 ≥80%：高波动区间，历史上对应恐慌底部
- 🟢 ≤20%：低波动区间，市场过于乐观
- 灰色 20-80%：正常区间
| 现有文件 | **不变** | market_monitor.py / dashboard/app.py 等均不修改 |

移除计划（不再需要）：
- ~~`Dockerfile.push`~~
- ~~`requirements.push.txt`~~

---

## 七、主要风险与处理

| 风险 | 概率 | 处理方式 |
|------|------|---------|
| ~~akshare 被海外 IP 拒绝~~ | ~~中高~~ | **已消除**：ap-southeast-2 实测全部通过 |
| 企业微信 Webhook URL 泄露 | 低 | `familyfund.env` 不提交 git，chmod 600 |
| 节假日判断不准 | 低 | `akshare.tool_trade_date_hist_sina()` 有完整交易日历 |
| 数据拉取超时 | 低 | 各标的独立 try/except，单个失败不影响整体推送 |
| EC2 实例停止/重启后 cron 丢失 | 低 | cron 写入 crontab 持久化，重启后自动恢复 |

---

## 七、待确认事项

1. **推送时间**：方案中为 8:30，是否合适？
2. **节假日处理**：是否需要在节假日前一天发"明日休市"提示？
3. **美股单独推送**：是否需要额外一条美股收盘后推送（UTC 22:00，北京次日 06:00）？

---

## 八、实施阶段

| 阶段 | 内容 | 前置条件 |
|------|------|---------|
| P1 | 实现 `notifier.py` + `daily_push.py`，本地测试推送 | Webhook URL ✅ 已验证通过 |
| P2 | EC2 上执行 `ec2_setup.sh`，手动触发验证 | P1 完成 |
| P3 | 等第一个交易日 cron 自动触发，确认端到端正常 | P2 完成 |
