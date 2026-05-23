# EC2 ↔ iCloud 数据同步指南

**最后更新：** 2026-05-23

---

## 概述

FamilyFund 的"市场温度计"和"基本面历史"数据由 EC2 每日定时采集（`scripts/daily_push.py`），存放在 EC2 本地 `~/data/`。Mac 端通过 SCP 拉取到 iCloud，两台 Mac 自动同步。

**这是单向同步：EC2（写入）→ Mac（只读）。** Mac 上不应直接修改这些文件，否则会被下次 SCP 覆盖。

---

## 数据源职责划分

| 文件 | 写方 | 读方 | 内容 |
|------|------|------|------|
| `market_cache.json` | EC2 daily_push | Mac dashboard、daily_push 离线兜底 | 市场温度计每日缓存（PE/VIX/QVIX 等当前值）|
| `vol_history.json` | EC2 daily_push | Mac dashboard 矩阵分位计算 | VIX/QVIX/VXN 历史快照 |
| `fundamentals_history.json` | EC2 daily_push | Mac dashboard 个股 PE/PB 分位 | 基本面历史（PE/PB/ROE 等 11 字段） |
| `portfolio.csv` | **Mac 用户**（iCloud 主写）| EC2 不读 | 持仓快照，与 EC2 无关 |
| `watch_symbols.json` | **Mac 用户**配置 | EC2 daily_push 读取 | 宽基 ETF 监控列表（VOO/QQQ 等） |
| `yf_symbols.json` | **Mac 用户**配置 | EC2 daily_push 读取（不存在则用默认值）| 持仓个股的 yfinance symbol 映射 |

**反向同步（Mac → EC2）需手动 SCP**，仅适用于 `watch_symbols.json` / `yf_symbols.json` 等配置文件。

---

## 一、Mac → EC2 同步配置文件

新增 / 修改 `watch_symbols.json` 后：

```bash
scp -i ~/PEM/Haolun-AWS.pem \
  "$FAMILYFUND_DATA/watch_symbols.json" \
  ec2-user@ec2-3-107-26-196.ap-southeast-2.compute.amazonaws.com:~/data/
```

EC2 下次 daily_push 跑时会读到新配置。

---

## 二、EC2 → Mac 拉取数据（ff-pull）

`~/.zshrc` 里定义了 `ff-pull` 函数：

```bash
# FamilyFund: pull EC2 market data to iCloud
_EC2="ec2-user@ec2-3-107-26-196.ap-southeast-2.compute.amazonaws.com"
_PEM="-i $HOME/PEM/Haolun-AWS.pem"
_ICLOUD="/Users/I849833/Library/Mobile Documents/com~apple~CloudDocs/Project_shared_files/FamilyFund/data"
ff-pull() {
    echo "Pulling from EC2..."
    scp ${=_PEM} "$_EC2:~/data/market_cache.json" "$_ICLOUD/market_cache.json" && echo "  ✓ market_cache.json"
    scp ${=_PEM} "$_EC2:~/data/fundamentals_history.json" "$_ICLOUD/fundamentals_history.json" 2>/dev/null && echo "  ✓ fundamentals_history.json" || echo "  - fundamentals_history.json (not yet)"
    scp ${=_PEM} "$_EC2:~/data/vol_history.json" "$_ICLOUD/vol_history.json" 2>/dev/null && echo "  ✓ vol_history.json" || echo "  - vol_history.json (not yet)"
    echo "Done."
}
```

**注意点：**
- 用 `$HOME/PEM/...` 而不是 `~/PEM/...`（zsh 在变量里不展开 `~`）
- 用 `${=_PEM}` 强制 word splitting（避免 `-i path` 被当成单参数）

**使用：**

```bash
ff-pull
```

**输出示例：**

```
Pulling from EC2...
  ✓ market_cache.json
  ✓ fundamentals_history.json
  ✓ vol_history.json
Done.
```

---

## 三、EC2 部署流程（代码升级时）

每次代码改动后，EC2 需要 `git pull` + 验证：

### 1. SSH 进 EC2

```bash
ec2  # alias for ssh -i ~/PEM/Haolun-AWS.pem ec2-user@...
```

### 2. 拉最新代码

```bash
cd ~/familyfund
git pull
```

### 3. 验证 daily_push 可跑

```bash
source ~/familyfund.env  # 读取 FAMILYFUND_DATA, WXWORK_WEBHOOK_URL
~/familyfund-venv/bin/python scripts/daily_push.py --force
```

`--force` 跳过交易日检查。预期输出：

```
INFO 拉取市场数据 (force_refresh=True)...
INFO 基本面快照已更新: {'updated': 7, 'skipped_today': 0, 'errors': 0}
INFO QVIX 历史快照已更新
INFO 发送推送...
INFO 推送完成
```

### 4. 配置文件需同步（如有变化）

如果新代码引入了新配置文件（如 `watch_symbols.json`），先从 Mac 上传：

```bash
# 在 Mac 上：
scp -i ~/PEM/Haolun-AWS.pem \
  "$FAMILYFUND_DATA/watch_symbols.json" \
  ec2-user@<host>:~/data/
```

---

## 四、EC2 cron 配置

EC2 上有 cron 任务每日触发 daily_push（具体配置见 EC2 上 `crontab -l`）。

cron 跑时机：**北京时间早上 8:30 左右**（A 股开盘前），确保推送在你打开 Dashboard 之前。

cron 失败排查：

```bash
ec2
crontab -l       # 看 cron 配置
ls ~/logs        # 看 daily_push 日志
tail ~/logs/daily_push.log
```

---

## 五、数据迁移 / 历史保留

### 旧文件归档命名规范

代码升级导致数据格式变化时（如 `pe_history_us.json` → `fundamentals_history.json`），旧文件用 `.archived` 后缀保留：

```bash
mv ~/data/pe_history_us.json ~/data/pe_history_us.json.archived
```

不在 ff-pull 里同步 `.archived` 文件（它们只是历史备份）。

### 历史数据迁移

参考 2026-05-23 的迁移脚本（已删除，但流程记录）：
1. 读旧文件
2. 把数据合并到新文件（按 date 去重）
3. 按 date 排序
4. 原子写入新文件
5. 旧文件改名 `.archived`

---

## 六、安全约定

- **凭证不入 git**：`familyfund.env`（含 `WXWORK_WEBHOOK_URL`）只在 EC2 本地，不上传任何代码仓库
- **数据文件不入 git**：所有 `*.json` 数据文件存 iCloud 或 EC2 本地，`.gitignore` 已配置
- **PEM 文件**：`~/PEM/Haolun-AWS.pem` 只在 Mac 本地，**永远不要上传任何地方**

---

## 七、常见问题

### ff-pull 报"Permission denied"

```
Warning: Identity file ~/PEM/Haolun-AWS.pem not accessible
ec2-user@...: Permission denied (publickey)
```

**原因**：zsh 变量里 `~` 不展开。
**解决**：检查 `.zshrc` 里 `_PEM="-i $HOME/PEM/..."`（不是 `~`），且 scp 命令用 `${=_PEM}` 而非 `$_PEM`。

### EC2 daily_push 报 "FAMILYFUND_DATA not set"

```bash
# 跑前先 source env
source ~/familyfund.env
~/familyfund-venv/bin/python scripts/daily_push.py
```

### 网络问题：EC2 拉 yfinance / akshare 超时

EC2 在新加坡（ap-southeast-2），到 Yahoo / akshare 通常稳定。如遇超时：
1. 检查 EC2 安全组是否允许出站
2. 直接在 EC2 上 `curl https://query1.finance.yahoo.com` 测试
3. 通常 1-2 小时后自愈（外部 API 偶发限流）

### Mac 拉的数据是旧的

EC2 cron 还没跑（每天早 8:30 才触发）。手动触发：

```bash
ec2 'source ~/familyfund.env && ~/familyfund-venv/bin/python ~/familyfund/scripts/daily_push.py --force'
ff-pull
```

---

## 八、未来可能的演进

1. **自动化 ff-pull**：Mac 加个 launchd / cron 每日早上 9:00 自动跑（确保 EC2 cron 已完成）
2. **健康监控**：EC2 daily_push 失败时发企业微信告警（当前是静默 warning）
3. **数据完整性校验**：ff-pull 后自动检查 fundamentals_history.json 是否新增了今日记录

不做：
- ❌ Git push 数据到 GitHub（仓库膨胀，收益边际，详见 OPEN_POINTS_PORTFOLIO_DESIGN.md 的讨论）
- ❌ 双向同步 / EC2 写 iCloud（EC2 没法挂 iCloud；多写者会冲突）

---

## 九、相关文档

- `docs/DESIGN_DAILY_PUSH.md` — 推送内容设计
- `docs/DESIGN_MARKET_MONITOR.md` — 市场温度计数据结构
- `src/fundamentals.py` — `append_fundamentals_snapshot` 实现
- `src/market_monitor.py` — `append_vol_snapshot` 实现
- `scripts/daily_push.py` — EC2 cron 入口
