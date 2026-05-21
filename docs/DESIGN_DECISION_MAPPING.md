# 研报库决策映射 — 设计文档

> **版本**: v0.1
> **创建日期**: 2026-05-21
> **状态**: 设计阶段，待实现
> **关联文档**: `DESIGN_RESEARCH_LIBRARY.md`

---

## 一、目标

在 Research Tab 的每个标的旁边显示当前的买卖决策（基于最新芒格分析），并维护历史变更时间线。

让 "看好标的 → 创建文件夹+财报 → Claude 分析 → Dashboard 直接看到决策" 变成一个高度自动化的闭环。

---

## 二、用户工作流

### 2.1 完整流程

```
[Step 1 手工] 用户在 iCloud 中创建标的文件夹 + 下载近期财报 PDF
   ↓
[Step 2 手工触发] 用户对 Claude Code 说："分析 XXX"
   ↓
[Step 3 Claude 自动化]
   ├─ 读取 PDF 财务数据
   ├─ yfinance 拉取实时基本面（含同业对比）
   ├─ 港 A 双上市：额外抓取双市场数据 + AH 价差
   ├─ 调用 munger-perspective skill 完成框架分析
   ├─ 写入 analysis/*.md（文档末尾含结构化决策块）
   └─ 更新 _meta/decisions.json
   ↓
[Step 4 自动] Dashboard Research Tab 显示决策标签 + 横幅
```

### 2.2 用户最少需要做的事

- 在 `_watchlist/` 下创建 `中文名（代码）/reports/` 并放入财报 PDF
- 对 Claude 说一句"分析 XXX"

剩下全自动。

---

## 三、决策类型枚举

| 决策 | 颜色标签 | 含义 |
|------|---------|------|
| 🟢 买入 | green | 当前未持有，建议建仓 |
| 🟢 加仓 | green | 已持仓，建议增加 |
| 🔵 持有 | blue | 已持仓，维持不动 |
| 🟡 观察 | yellow | 暂不操作，监控基本面 |
| 🟠 减仓 | orange | 已持仓，建议部分卖出 |
| 🔴 卖出 | red | 已持仓，建议清仓 |
| ⚪ 不感兴趣 | grey | 评估后排除 |

### 港 A 双上市的市场标注

| Market 字段 | 含义 |
|------------|------|
| `A股` | 仅 A 股市场 |
| `H股` | 仅 H 股市场 |
| `N/A` | 单一市场上市，无需选择 |

---

## 四、数据存储

### 4.1 文件位置

`$FINANCE_REPORTS_DIR/_meta/decisions.json`

### 4.2 数据结构

```json
{
  "成都银行（601838.SS）": {
    "current": {
      "action": "持有",
      "market": "A股",
      "date": "2026-05-18",
      "summary": "PB 0.85x 配 ROE 15.39%，外资股东岿然不动",
      "source_doc": "成都银行投资审视：芒格框架分析.md"
    },
    "history": [
      {
        "action": "买入",
        "market": "A股",
        "date": "2026-04-10",
        "summary": "建仓信号：城商行龙头折价",
        "source_doc": "成都银行投资审视：芒格框架分析.md"
      }
    ]
  },
  "招商银行（600036.SS）": {
    "current": {
      "action": "买入",
      "market": "A股",
      "date": "2026-05-21",
      "summary": "A 股 PB 0.83x，零股息税长期持有，单仓上限 5%",
      "source_doc": "招商银行投资审视：芒格框架分析.md"
    },
    "history": []
  }
}
```

### 4.3 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `action` | string | 7 种决策类型之一 |
| `market` | string | A股 / H股 / N/A |
| `date` | YYYY-MM-DD | 决策生效日期 |
| `summary` | string | ≤50字一句话摘要 |
| `source_doc` | string | 对应 analysis/ 下的 md 文件名 |

---

## 五、分析文档约定

### 5.1 文档末尾必须包含决策记录块

每篇芒格分析文档末尾必须有 `## 决策记录` 章节，含一个 yaml 代码块：

````markdown
## 决策记录

```yaml
action: 买入
market: A股
date: 2026-05-21
summary: A 股 PB 0.83x，零股息税长期持有，单仓上限 5%
```
````

这是 Claude 自动更新 `decisions.json` 的输入源。

### 5.2 已有文档的兼容性

`成都银行投资审视：芒格框架分析.md` 等已写完的文档需要补一个决策记录块（一次性手工补齐）。

---

## 六、Dashboard 端设计

### 6.1 左列标的列表

每个标的名后追加决策标签（带颜色）：

```
🔴 持仓中
  • 成都银行（601838.SS） 🔵 持有
  • 思爱普（SAP）         🔵 持有
  • 腾讯控股（00700.HK）  🟡 观察

👁 观察中
  • 招商银行（600036.SS） 🟢 买入·A股   ← 强信号
  • 阿里巴巴（09988.HK）  ⚪ 不感兴趣
  • 农夫山泉（09633.HK）  🟡 观察
```

### 6.2 右列决策横幅

选中标的后，分析文档区上方显示醒目横幅：

```
┌──────────────────────────────────────────────┐
│ 🟢 买入 · A股        2026-05-21              │
│ A 股 PB 0.83x，零股息税长期持有，单仓上限 5%   │
│ 来源：招商银行投资审视：芒格框架分析            │
│ [查看历史 ▼]                                  │
└──────────────────────────────────────────────┘
```

`[查看历史 ▼]` 展开后显示历史决策时间线（来自 `history` 字段）。

### 6.3 编辑入口（第二阶段）

横幅旁加 `✏️ 编辑决策` 按钮，点击后弹出表单：

| 字段 | 控件 |
|------|------|
| Action | selectbox（7 选 1） |
| Market | selectbox（A股/H股/N/A） |
| Date | date_input |
| Summary | text_input |
| Source Doc | selectbox（自动列出该标的 analysis/ 下所有 md） |

保存时自动把当前 `current` 归档到 `history`，新值写入 `current`。

---

## 七、新增模块

### 7.1 `src/research_library.py` 扩展

| 函数 | 说明 |
|------|------|
| `load_decisions(reports_dir)` | 读取 `_meta/decisions.json`，缺失时返回 `{}` |
| `get_decision(reports_dir, folder_name)` | 返回某标的的 `current` 决策（dict 或 None） |
| `get_decision_history(reports_dir, folder_name)` | 返回 `history` 列表 |
| `update_decision(reports_dir, folder_name, action, market, date, summary, source_doc)` | 写入新决策；旧 current 自动归档到 history |

### 7.2 装饰器与缓存

加上现有的 `@st.cache_data(ttl=60)` 包装，与其他 Research Tab 函数一致。

### 7.3 决策标签辅助函数

```python
DECISION_COLORS = {
    "买入":   "🟢",
    "加仓":   "🟢",
    "持有":   "🔵",
    "观察":   "🟡",
    "减仓":   "🟠",
    "卖出":   "🔴",
    "不感兴趣": "⚪",
}

def format_decision_badge(decision: dict) -> str:
    """返回 '🟢 买入·A股' 这样的简短标签字符串"""
```

---

## 八、CLAUDE.md 工作流规则（新增章节）

需要在 `CLAUDE.md` 中加入一节 `## 个股研报分析工作流`，指定 Claude 在用户说"分析 XXX"时的固定操作流程。该章节内容在实施时一并写入 `CLAUDE.md`，本设计文档的第二节是其简化版。

关键规则：

1. **路径规范**：分析文档必须写入 `<标的文件夹>/analysis/<中文名>投资审视：芒格框架分析.md`
2. **决策块格式**：文档末尾必须含 `## 决策记录` + yaml 块（结构见第五节）
3. **港 A 双上市**：必须分析 A 股 vs H 股的市场选择（股息税、流动性、AH 价差、汇率）
4. **更新 decisions.json**：写完文档后，必须读取并更新该文件，旧 current 归档到 history
5. **更新 ticker_map.json**：首次分析的新标的，必须添加到 ticker_map.json 对应分组
6. **完成提示**：分析完成后告诉用户"到 Research Tab 点🔄 刷新即可看到"

---

## 九、实施分阶段

### 第一阶段（先做）

1. 写 `CLAUDE.md` 工作流规则
2. 实现 `research_library.py` 的决策相关函数
3. Research Tab 左列显示决策标签
4. Research Tab 右列顶部决策横幅 + 历史时间线（只读）
5. 已有文档（成都银行、招行、SAP、腾讯）补齐 `## 决策记录` 块
6. 创建初始 `_meta/decisions.json`

### 第二阶段（验证后做）

1. 用招行/成都银行/SAP 走一遍完整流程，验证 CLAUDE.md 规则是否清晰
2. 跑通后实现 Dashboard 内编辑 UI（带表单）

---

## 十、待决策

| 问题 | 选项 | 倾向 |
|------|------|------|
| 决策时间线展示方式 | 折叠 expander vs 一直展开 | 折叠 |
| 编辑历史是否可删除 | 仅追加 vs 可删 | 仅追加（保留可追溯性） |
| 标的没有 decisions 时显示什么 | 留空 vs `❓ 未评估` | `❓ 未评估` |
| 决策摘要长度限制 | 50 字硬限制 vs 软建议 | 软建议（UI 显示前 50 字） |
