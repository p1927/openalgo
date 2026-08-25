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
