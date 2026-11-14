"""Shared parser for code-prefixed DCA text imports."""
import pandas as pd


def parse_dca_text(text, template_df):
    """
    解析定投文本格式，返回 (entries, errors)。
    格式：
        code: 022523
        100,45.14,2.2155
        100,45.56,2.1947

        code: 007194
        10000,7944.05,1.2583
    每行三列：金额(CNY), 份额, 净值。
    只支持已建仓标的（code 必须存在于 template_df）。
    """
    import re as _re
    entries = []
    errors = []

    # 构建 code → name 映射（排除 Cash）
    code_to_name = {
        str(row['Code']): str(row['Name'])
        for _, row in template_df[template_df['Asset_Class'] != 'Cash'].iterrows()
        if pd.notna(row['Code']) and pd.notna(row['Name'])
    }

    # 按空行分块
    blocks = _re.split(r'\n\s*\n', text.strip())
    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue

        # 第一行必须是 code: xxx
        code_match = _re.match(r'^code\s*:\s*(\S+)$', lines[0], _re.IGNORECASE)
        if not code_match:
            errors.append(f"无法识别代码行：「{lines[0]}」")
            continue

        code = code_match.group(1)
        if code not in code_to_name:
            errors.append(f"代码 {code} 不在当前持仓中，跳过")
            continue

        name = code_to_name[code]

        # 后续每行解析三列
        for row_idx, row_line in enumerate(lines[1:], start=1):
            parts = row_line.split(',')
            if len(parts) != 3:
                errors.append(f"{code} 第{row_idx}行列数不对（需3列）：「{row_line}」")
                continue
            try:
                amount = float(parts[0])
                shares = float(parts[1])
                nav    = float(parts[2])
            except ValueError:
                errors.append(f"{code} 第{row_idx}行数值解析失败：「{row_line}」")
                continue

            entries.append({
                'type':        '买入',
                'asset_name':  name,
                'asset_label': f"{name} ({code})",
                'asset_code':  code,
                'amount':      amount,
                'price':       nav,
                'fee':         0.0,
                'shares':      shares,
                'is_new':      False,
                'new_asset':   {},
            })

    return entries, errors

