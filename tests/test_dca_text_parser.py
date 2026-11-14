from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dca_text_parser import parse_dca_text


def holdings():
    return pd.DataFrame([
        dict(Code='021000', Name='南方纳指100 I类', Asset_Class='US_Growth_Fund'),
        dict(Code='007194', Name='长城短债', Asset_Class='Fixed_Income'),
        dict(Code='CASH', Name='现金', Asset_Class='Cash'),
    ])


def test_codes_keep_leading_zeros_and_same_code_preserves_each_trade():
    entries, errors = parse_dca_text('code: 021000\n200,88.47,2.2607\n200,88.47,2.2607', holdings())
    assert not errors
    assert len(entries) == 2
    assert entries[0]['asset_code'] == '021000'
    assert entries[0]['asset_name'] == '南方纳指100 I类'
    assert entries[0]['amount'] == 200 and entries[0]['shares'] == 88.47
    assert entries[0]['price'] == 2.2607


def test_multiple_blocks_and_errors_do_not_discard_valid_rows():
    text = 'CODE: 021000\n200,80,2.5\nbad,row\n200,no,2.5\n\ncode: 007194\n100,90,1.11\n\ncode: CASH\n100,100,1\n\ncode: 999999\n100,50,2'
    entries, errors = parse_dca_text(text, holdings())
    assert [e['asset_code'] for e in entries] == ['021000', '007194']
    assert len(errors) == 4


def test_empty_or_missing_header_does_not_create_transactions():
    assert parse_dca_text('', holdings()) == ([], [])
    entries, errors = parse_dca_text('200,80,2.5', holdings())
    assert not entries and len(errors) == 1
