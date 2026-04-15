# 设计方案：市场温度计每日推送

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

在 EC2 上存放于 `~/familyfund.env`，cron 运行时 `source` 载入。

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
├── data/                # market_cache.json 缓存目录
└── logs/
    └── push.log
```

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

# 3. 创建 venv 并安装依赖（精简版，只装推送所需包）
python3 -m venv $BASE/familyfund-venv
$BASE/familyfund-venv/bin/pip install --quiet akshare yfinance pandas requests

# 4. 创建目录
mkdir -p $BASE/data $BASE/logs

# 5. 创建 .env 文件（需手动填入 Webhook URL）
cat > $BASE/familyfund.env << 'ENVEOF'
export WXWORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY_HERE
export FAMILYFUND_DATA=/home/ec2-user/data
ENVEOF
chmod 600 $BASE/familyfund.env  # 仅 owner 可读

# 6. 配置 cron（UTC 00:30 = 北京时间 08:30）
CRON_LINE="30 0 * * * source $BASE/familyfund.env && $BASE/familyfund-venv/bin/python $BASE/familyfund/scripts/daily_push.py >> $BASE/logs/push.log 2>&1"
(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -

echo ""
echo "Setup complete."
echo ">>> 请编辑 $BASE/familyfund.env，填入真实的 WXWORK_WEBHOOK_URL <<<"
echo "手动测试推送: source $BASE/familyfund.env && $BASE/familyfund-venv/bin/python $BASE/familyfund/scripts/daily_push.py"
```

---

## 五、文件变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/notifier.py` | 新增 | 消息格式化 + 企业微信 Webhook 发送 |
| `scripts/daily_push.py` | 新增 | 推送入口，判断交易日 |
| `scripts/test_ec2_akshare.py` | 已有 | EC2 akshare 可达性测试（已验证通过）|
| `deploy/ec2_setup.sh` | 新增 | EC2 一键初始化（venv + cron）|
| 现有文件 | **不变** | market_monitor.py / dashboard/app.py 等均不修改 |

移除计划（不再需要）：
- ~~`Dockerfile.push`~~
- ~~`requirements.push.txt`~~

---

## 六、主要风险与处理

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
