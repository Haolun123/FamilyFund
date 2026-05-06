# DESIGN: 定投管理模块（DCA Manager）

> **状态**：待实现（P2）
> **日期**：2026-05-06

---

## 功能定位

各平台定投计划的统一查看入口。FamilyFund 不控制实际扣款（各平台自行执行），但提供：

1. **本周建议定投金额** — 基础金额 × 市场温度计倍数
2. **各平台汇总** — 一眼看清本周总投入计划
3. **配置管理** — UI 内增删改定投计划，不需要手动编辑 JSON

执行完毕后，用户按正常流程维护 Weekly Update，无额外步骤。

---

## 配置数据结构

### `$FAMILYFUND_DATA/dca_config.json`

```json
{
  "plans": [
    {
      "id": "plan_001",
      "name": "博时标普500",
      "code": "018738",
      "asset_class": "US_Blend_Fund",
      "platform": "支付宝",
      "base_amount_cny": 800,
      "frequency": "weekly",
      "enabled": true,
      "note": ""
    },
    {
      "id": "plan_002",
      "name": "南方纳指100",
      "code": "021000",
      "asset_class": "US_Growth_Fund",
      "platform": "天天基金",
      "base_amount_cny": 500,
      "frequency": "weekly",
      "enabled": true,
      "note": ""
    },
    {
      "id": "plan_003",
      "name": "南方中证A500",
      "code": "022434",
      "asset_class": "CN_Index_Fund",
      "platform": "天天基金",
      "base_amount_cny": 500,
      "frequency": "weekly",
      "enabled": true,
      "note": ""
    },
    {
      "id": "plan_004",
      "name": "招行积存金",
      "code": "GOLD",
      "asset_class": "Gold",
      "platform": "招商银行",
      "base_amount_cny": 1500,
      "frequency": "weekly",
      "enabled": true,
      "unit": "gram",
      "base_amount_unit": 2,
      "min_unit": 1,
      "note": "最小单位1g，建议金额自动换算为整克数"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `id` | 唯一标识，自动生成 |
| `name` | 标的名称（自由填写）|
| `code` | 基金代码（用于天天基金净值查询）|
| `asset_class` | 资产类别，8选1，用于匹配温度计倍数 |
| `platform` | 执行平台（自由填写：支付宝/天天基金/招商银行等）|
| `base_amount_cny` | 基础定投金额（正常市场下每周投入，人民币）|
| `frequency` | `weekly` / `biweekly` / `monthly` |
| `enabled` | 是否启用（临时暂停用）|
| `unit` | 可选，`gram`表示按克计量（积存金专用），缺省则按人民币 |
| `base_amount_unit` | 可选，基础买入克数（`unit=gram` 时使用）|
| `min_unit` | 可选，最小交易单位（克），低于此则建议暂停 |
| `note` | 备注（可选）|

> **黄金特殊处理**：招行积存金最小单位为1g，建议金额换算为整克数展示（如"本周买2g"）。
> 实际操作完成后，Weekly Update 仍按**实际成交金额（人民币）**填写 NCF，与此处的克数建议完全独立，无任何耦合。

---

## 温度计倍数映射

复用 `market_monitor.py` 中已有的倍数计算，按 `asset_class` 匹配：

| asset_class | 使用的温度计信号 |
|-------------|---------------|
| `US_Blend_Fund` | 标普500信号（PE_SP500 × VIX）|
| `US_Growth_Fund` | 纳指100信号（PE_NDX100 × VIX）|
| `CN_Index_Fund` | 沪深300 或 中证A500 信号（QVIX）|
| `ETF_Stock` | 沪深300信号（默认）|
| `Gold` | 黄金信号（MA200乖离率 × VIX）|
| `Fixed_Income` | 无倍数，始终 1.0x |
| `Company_Stock` | 无倍数，始终 1.0x |

本周建议金额 = `base_amount_cny × multiplier`，结果取整到10元。

---

## Dashboard UI

### 位置
Tab5（Market Monitor）底部新增"DCA Plan"折叠区。

**语义理由**：DCA Manager 是事前决策参考（看信号 → 决定本周怎么投），Market Monitor 是同一场景的上下文（市场信号 + 定投行动建议，构成完整决策闭环）。Tab2 Weekly Update 是事后录入，时序相反，不适合放在那里。

### 展示区（只读，每次打开自动刷新）

```
─── 本周定投计划 ──────────────────────────────────────
市场信号更新时间：2026-05-06 09:30

标的          平台        基础金额   信号      本周建议
博时标普500   支付宝      ¥800      1.5x ↑    ¥1,200
南方纳指100   天天基金    ¥500      1.5x ↑    ¥750
南方中证A500  天天基金    ¥500      2.0x ↑↑   ¥1,000
黄金          招商银行    ¥300      0.5x ↓    ¥150

                          本周建议总投入：¥3,100
                          本月已投（含本周）：¥5,800 / 预算 ¥8,000

[⚙ 管理定投计划]  按钮 → 展开配置区
```

信号箭头说明：`↑↑` 强买（≥2x）、`↑` 加仓（1.2-2x）、`→` 正常（0.8-1.2x）、`↓` 减少（<0.8x）

### 配置区（可折叠，默认收起）

```
─── 管理定投计划 ──────────────────────────────────────
[＋ 新增定投计划]

  ┌─ 博时标普500 ────────────────────────── [启用●] [删除] ─┐
  │ 平台：[支付宝          ] 基础金额：[¥800  ] 频率：[每周▼] │
  │ 资产类别：[US_Blend_Fund▼]  备注：[              ]       │
  └──────────────────────────────────────────────────────┘

  ┌─ 南方纳指100 ───────────────────────── [启用●] [删除] ─┐
  │ ...                                                     │
  └──────────────────────────────────────────────────────┘

[保存配置]
```

新增计划时，标的名称/代码/平台均为文本输入，资产类别为下拉选择。

---

## 与其他模块的联动

| 模块 | 联动方式 |
|------|---------|
| 市场温度计 | 直接调用 `get_market_data()` 获取最新倍数，零新增逻辑 |
| 储蓄率（待实现）| 本周建议总投入 / 月收入，提示是否超出储蓄预算 |
| Weekly Update | 无强耦合，用户执行完定投后正常维护 portfolio.csv |
| 天天基金接口 | 可选：展示各标的本周最新净值（`DESIGN_ANALYTICS.md` 中已规划）|

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `$FAMILYFUND_DATA/dca_config.json` | 定投计划配置（UI 驱动，用户可增删改）|
| `dashboard/app.py` | Tab5（Market Monitor）底部新增 DCA Plan 折叠区 |
| `src/dca_manager.py`（可选）| 计划加载、建议金额计算逻辑（也可内联在 app.py）|

---

## 实现顺序

1. `dca_config.json` 数据结构 + 加载/保存函数
2. 展示区：读取配置 + 调用温度计倍数 → 渲染汇总表
3. 配置区：增删改 UI（复用再平衡建议的 session_state 模式）
4. 可选：月度预算联动（等储蓄率模块实现后接入）
