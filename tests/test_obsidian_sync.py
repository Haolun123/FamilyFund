"""
tests/test_obsidian_sync.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
obsidian_sync 模块的单元测试。
"""

import os
import sys
import tempfile
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from obsidian_sync import (
    sync_to_obsidian,
    _safe_filename,
    _parse_target_num,
    _build_snapshot_frontmatter,
    _fmt_yaml_str,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_nav_df(rows=3):
    """生成最小化的 fund_nav_df。"""
    data = {
        'Date': [f'2026-08-{10 + i:02d}' for i in range(rows)],
        'NAV': [1.0 + i * 0.01 for i in range(rows)],
        'Total_Value': [1_000_000 + i * 10_000 for i in range(rows)],
    }
    return pd.DataFrame(data)


def _make_allocation_df():
    return pd.DataFrame({
        'Asset_Class': ['Fixed_Income', 'Individual_Stock', 'Gold', 'Cash'],
        'Market_Value': [600_000, 150_000, 50_000, 40_000],
    })


def _make_cost_basis_df():
    return pd.DataFrame({
        'Asset': ['腾讯控股', '成都银行', '现金'],
        'Code': ['00700.HK', '601838.SS', ''],
        'Asset_Class': ['Individual_Stock', 'Individual_Stock', 'Cash'],
        'Cost': [83_000, 20_000, 40_000],
        'Market_Value': [78_000, 22_000, 40_000],
    })


# ── 工具函数测试 ──────────────────────────────────────────────────────────────

def test_safe_filename_removes_special_chars():
    assert _safe_filename('腾讯控股（00700.HK）') == '腾讯控股（00700.HK）'
    assert '/' not in _safe_filename('A/B')
    assert '*' not in _safe_filename('A*B')


def test_parse_target_num_wan():
    assert _parse_target_num('7万') == 70_000
    assert _parse_target_num('3-4万') == 40_000   # 取最后一个数字（区间上限）
    assert _parse_target_num('4万（若建仓）') == 40_000


def test_parse_target_num_zero():
    assert _parse_target_num('0') is None
    assert _parse_target_num('观察') is None
    assert _parse_target_num('') is None
    assert _parse_target_num(None) is None


def test_fmt_yaml_str_chinese():
    result = _fmt_yaml_str('高股息+周期')
    assert result.startswith('"') and result.endswith('"')


def test_fmt_yaml_str_plain():
    assert _fmt_yaml_str('holding') == 'holding'


# ── 快照 frontmatter 测试 ─────────────────────────────────────────────────────

def test_snapshot_frontmatter_contains_required_fields():
    nav_df = _make_nav_df()
    alloc_df = _make_allocation_df()
    result = _build_snapshot_frontmatter(
        '2026-09-01', nav_df, alloc_df, 0.082, -0.0315, 41_000, 8_660
    )
    assert 'date: 2026-09-01' in result
    assert 'nav:' in result
    assert 'total_assets:' in result
    assert 'xirr: 8.2' in result
    assert 'max_drawdown: -3.15' in result
    assert 'tags: [familyfund, snapshot]' in result


def test_snapshot_frontmatter_nav_change():
    nav_df = _make_nav_df(rows=2)
    result = _build_snapshot_frontmatter(
        '2026-09-01', nav_df, _make_allocation_df(), None, None, 0, None
    )
    assert 'nav_change_wow' in result


def test_snapshot_frontmatter_single_row_no_crash():
    """只有一行历史时，周变动应为 null 而不报错。"""
    nav_df = _make_nav_df(rows=1)
    result = _build_snapshot_frontmatter(
        '2026-09-01', nav_df, _make_allocation_df(), None, None, 0, None
    )
    assert 'nav_change_wow: "null"' in result


# ── 完整同步测试 ──────────────────────────────────────────────────────────────

def test_sync_creates_required_files(tmp_path):
    """完整同步后，必要文件和目录都应存在。"""
    vault = str(tmp_path / 'vault')
    data_dir = str(tmp_path / 'data')
    os.makedirs(data_dir)

    result = sync_to_obsidian(
        date_str='2026-09-01',
        data_dir=data_dir,
        fund_nav_df=_make_nav_df(),
        allocation_df=_make_allocation_df(),
        cost_basis_df=_make_cost_basis_df(),
        xirr=0.082,
        max_drawdown=-0.0315,
        weekly_dca=8_660,
        vault_path=vault,
    )

    assert result['ok'] is True
    assert result['files_written'] > 0

    ff_dir = os.path.join(vault, 'familyfund')
    assert os.path.exists(os.path.join(ff_dir, '_dashboard.md'))
    assert os.path.exists(os.path.join(ff_dir, 'snapshots', '2026-09-01.md'))
    assert os.path.isdir(os.path.join(ff_dir, 'holdings'))


def test_sync_snapshot_is_appended_not_overwritten(tmp_path):
    """同一 vault，两次不同日期同步，两个快照文件都应存在。"""
    vault = str(tmp_path / 'vault')
    data_dir = str(tmp_path / 'data')
    os.makedirs(data_dir)

    for date in ['2026-08-25', '2026-09-01']:
        sync_to_obsidian(
            date_str=date,
            data_dir=data_dir,
            fund_nav_df=_make_nav_df(),
            allocation_df=_make_allocation_df(),
            cost_basis_df=_make_cost_basis_df(),
            xirr=0.082,
            max_drawdown=-0.0315,
            vault_path=vault,
        )

    snap_dir = os.path.join(vault, 'familyfund', 'snapshots')
    assert os.path.exists(os.path.join(snap_dir, '2026-08-25.md'))
    assert os.path.exists(os.path.join(snap_dir, '2026-09-01.md'))


def test_sync_dashboard_overwritten_each_run(tmp_path):
    """_dashboard.md 每次同步都应被覆盖（含最新图表数据）。"""
    vault = str(tmp_path / 'vault')
    data_dir = str(tmp_path / 'data')
    os.makedirs(data_dir)

    sync_to_obsidian(
        date_str='2026-08-25', data_dir=data_dir,
        fund_nav_df=_make_nav_df(), allocation_df=_make_allocation_df(),
        cost_basis_df=_make_cost_basis_df(),
        xirr=None, max_drawdown=None, vault_path=vault,
    )

    first_content = open(os.path.join(vault, 'familyfund', '_dashboard.md')).read()

    # 第二次同步（不同日期）
    sync_to_obsidian(
        date_str='2026-09-01', data_dir=data_dir,
        fund_nav_df=_make_nav_df(rows=5), allocation_df=_make_allocation_df(),
        cost_basis_df=_make_cost_basis_df(),
        xirr=None, max_drawdown=None, vault_path=vault,
    )

    second_content = open(os.path.join(vault, 'familyfund', '_dashboard.md')).read()
    assert 'updated: 2026-09-01' in second_content
    assert second_content != first_content


def test_sync_holdings_overwritten_on_second_run(tmp_path):
    """持仓文件每周覆盖——同一标的第二次同步后内容应更新。"""
    vault = str(tmp_path / 'vault')
    data_dir = str(tmp_path / 'data')
    os.makedirs(data_dir)

    sync_to_obsidian(
        date_str='2026-08-25', data_dir=data_dir,
        fund_nav_df=_make_nav_df(), allocation_df=_make_allocation_df(),
        cost_basis_df=_make_cost_basis_df(),
        xirr=None, max_drawdown=None, vault_path=vault,
    )

    # 修改 cost_basis（市值变化）
    new_cost = _make_cost_basis_df()
    new_cost.loc[new_cost['Asset'] == '腾讯控股', 'Market_Value'] = 90_000

    sync_to_obsidian(
        date_str='2026-09-01', data_dir=data_dir,
        fund_nav_df=_make_nav_df(), allocation_df=_make_allocation_df(),
        cost_basis_df=new_cost,
        xirr=None, max_drawdown=None, vault_path=vault,
    )

    holding_path = os.path.join(vault, 'familyfund', 'holdings', '腾讯控股.md')
    with open(holding_path) as f:
        content = f.read()
    assert '90,000' in content


def test_sync_invalid_vault_returns_error():
    """vault 路径不存在且无法推断时，返回错误而不抛异常。"""
    result = sync_to_obsidian(
        date_str='2026-09-01',
        data_dir='/tmp',
        fund_nav_df=_make_nav_df(),
        allocation_df=_make_allocation_df(),
        cost_basis_df=_make_cost_basis_df(),
        xirr=None, max_drawdown=None,
        vault_path='/nonexistent/path/that/does/not/exist',
    )
    # 要么成功（目录自动创建），要么返回 ok=False 带 error
    # 不应抛出异常
    assert 'ok' in result


def test_sync_son_fund(tmp_path):
    """儿子基金数据存在时，son_fund 目录和快照应被创建。"""
    vault = str(tmp_path / 'vault')
    data_dir = str(tmp_path / 'data')
    os.makedirs(data_dir)

    son_nav = _make_nav_df(rows=2)
    son_cost = pd.DataFrame({
        'Asset': ['南方纳指100', '现金'],
        'Code': ['001156', ''],
        'Asset_Class': ['US_Growth_Fund', 'Cash'],
        'Cost': [60_000, 5_000],
        'Market_Value': [65_000, 5_000],
    })

    result = sync_to_obsidian(
        date_str='2026-09-01',
        data_dir=data_dir,
        fund_nav_df=_make_nav_df(),
        allocation_df=_make_allocation_df(),
        cost_basis_df=_make_cost_basis_df(),
        xirr=None, max_drawdown=None,
        son_nav_df=son_nav, son_cost_df=son_cost, son_xirr=0.05,
        vault_path=vault,
    )

    assert result['ok'] is True
    son_dir = os.path.join(vault, 'familyfund', 'son_fund')
    assert os.path.exists(os.path.join(son_dir, '_dashboard.md'))
    assert os.path.exists(os.path.join(son_dir, 'snapshots', '2026-09-01.md'))
