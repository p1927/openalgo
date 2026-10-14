"""Tests for stock_simulator's scoped master-contract rebuild.

``delete_matching_symtoken_rows`` replaces a previous unconditional
``SymToken.query.delete()`` that wiped the *entire* ``symtoken`` table (shared
across all brokers, no ``broker`` column) on every stock_simulator rebuild --
including any other broker's independently-downloaded instruments (e.g. a
real broker's per-stock options chain). See
.claude/backlog/items/2026-08-25-options-microstructure-signal-gap.md's
2026-08-25 live-run verification entries for how this was found: a genuinely
live single-stock options-chain fetch kept failing because stock_simulator's
own rebuild (auto-triggered on every replay start) kept deleting a real
broker's F&O master contract out from under it.
"""

from __future__ import annotations

import pytest

from database.symbol import SymToken, db_session, init_db


@pytest.fixture(autouse=True)
def _clean_symtoken():
    init_db()
    SymToken.query.delete()
    db_session.commit()
    yield
    SymToken.query.delete()
    db_session.commit()


def _row(*, symbol, exchange, name):
    return SymToken(
        symbol=symbol, brsymbol=symbol, name=name, exchange=exchange, brexchange=exchange,
        token="1", expiry="", strike=0.0, lotsize=1, instrumenttype="", tick_size=0.05,
        contract_value=0,
    )


def test_other_brokers_rows_survive_a_scoped_rebuild():
    from broker.stock_simulator.database.master_contract_db import delete_matching_symtoken_rows

    other_broker_row = _row(symbol="RELIANCE28AUG2600CE", exchange="NFO", name="RELIANCE")
    own_stale_row = _row(symbol="NIFTY28AUG2624000CE", exchange="NFO", name="NIFTY")
    db_session.add_all([other_broker_row, own_stale_row])
    db_session.commit()

    # Simulate a stock_simulator rebuild that only ever produces NIFTY rows --
    # RELIANCE isn't one of its keys, so it must not be touched.
    delete_matching_symtoken_rows([("NIFTY28AUG2624000CE", "NFO")])

    remaining = {(r.symbol, r.exchange) for r in SymToken.query.all()}
    assert ("RELIANCE28AUG2600CE", "NFO") in remaining
    assert ("NIFTY28AUG2624000CE", "NFO") not in remaining


def test_own_rows_are_replaced_not_duplicated():
    import pandas as pd

    from broker.stock_simulator.database.master_contract_db import (
        copy_from_dataframe,
        delete_matching_symtoken_rows,
    )

    stale = _row(symbol="NIFTY28AUG2624000CE", exchange="NFO", name="NIFTY")
    db_session.add(stale)
    db_session.commit()

    fresh_rows = [
        {
            "symbol": "NIFTY28AUG2624000CE", "brsymbol": "NIFTY28AUG2624000CE", "name": "NIFTY",
            "exchange": "NFO", "brexchange": "NFO", "token": "2", "expiry": "28-AUG-26",
            "strike": 24000.0, "lotsize": 75, "instrumenttype": "CE", "tick_size": 0.05,
            "contract_value": 0,
        }
    ]
    delete_matching_symtoken_rows([("NIFTY28AUG2624000CE", "NFO")])
    copy_from_dataframe(pd.DataFrame(fresh_rows))

    rows = SymToken.query.filter(SymToken.symbol == "NIFTY28AUG2624000CE").all()
    assert len(rows) == 1
    assert rows[0].strike == 24000.0


def test_empty_keys_deletes_nothing():
    from broker.stock_simulator.database.master_contract_db import delete_matching_symtoken_rows

    row = _row(symbol="RELIANCE28AUG2600CE", exchange="NFO", name="RELIANCE")
    db_session.add(row)
    db_session.commit()

    delete_matching_symtoken_rows([])

    assert SymToken.query.count() == 1


def test_lazy_extend_replaces_only_the_keys_it_reinserts():
    from broker.stock_simulator.api.mc_extend import replace_symtoken_rows

    other_broker = _row(symbol="NIFTY28AUG2624500CE", exchange="NFO", name="NIFTY")  # same name/exchange/expiry
    other_broker.expiry = "28-AUG-26"
    stale = _row(symbol="NIFTY28AUG2624000CE", exchange="NFO", name="NIFTY")
    stale.expiry = "28-AUG-26"
    other_exchange = _row(symbol="NIFTY28AUG2624000CE", exchange="BFO", name="NIFTY")
    db_session.add_all([other_broker, stale, other_exchange])
    db_session.commit()

    fresh = {
        "symbol": "NIFTY28AUG2624000CE", "brsymbol": "NIFTY28AUG2624000CE", "name": "NIFTY", "exchange": "NFO",
        "brexchange": "NSE_FNO", "token": "9", "expiry": "28-AUG-26", "strike": 24000.0, "lotsize": 75,
        "instrumenttype": "CE", "tick_size": 0.05, "contract_value": 0,
    }
    replace_symtoken_rows([fresh], exchange="NFO")
    replace_symtoken_rows([fresh], exchange="NFO")  # idempotent: still one row for the key

    by_key = {}
    for r in SymToken.query.all():
        by_key.setdefault((r.symbol, r.exchange), []).append(r)
    assert len(by_key[("NIFTY28AUG2624000CE", "NFO")]) == 1 and by_key[("NIFTY28AUG2624000CE", "NFO")][0].token == "9"
    assert ("NIFTY28AUG2624500CE", "NFO") in by_key  # the other broker's strike for the same expiry survives
    assert ("NIFTY28AUG2624000CE", "BFO") in by_key  # a different exchange is not ours to touch


def test_lazy_extend_write_failure_rolls_back_and_raises():
    from broker.stock_simulator.api.mc_extend import replace_symtoken_rows

    keep = _row(symbol="NIFTY28AUG2624000CE", exchange="NFO", name="NIFTY")
    db_session.add(keep)
    db_session.commit()
    bad = {"symbol": "NIFTY28AUG2624000CE", "no_such_column": 1}
    with pytest.raises(Exception):
        replace_symtoken_rows([bad], exchange="NFO")
    assert SymToken.query.filter_by(symbol="NIFTY28AUG2624000CE").count() == 1  # the delete did not stick
