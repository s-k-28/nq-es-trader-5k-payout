"""Native atomic bracket tests for IBBroker.

Validates the core fix for the loss-cap breach: the protective stop must be
submitted *with* the entry (parent/child + transmit cascade) so it is live at
the broker the instant the position fills — no unprotected window.

Uses a lightweight ib_insync stub when the real package isn't installed, so the
mechanism is verified in any environment.
"""
from __future__ import annotations
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Inject a minimal ib_insync stub only if the real package is unavailable.
try:  # pragma: no cover - depends on environment
    import ib_insync  # noqa: F401
except Exception:  # pragma: no cover
    fake = types.ModuleType('ib_insync')

    class _Order:
        def __init__(self, action=None, totalQuantity=None, price=None):
            self.action = action
            self.totalQuantity = totalQuantity
            self.lmtPrice = price
            self.auxPrice = price
            self.orderId = None
            self.parentId = None
            self.tif = None
            self.ocaGroup = None
            self.ocaType = None
            self.transmit = True

    fake.IB = MagicMock
    fake.Contract = lambda **k: MagicMock()
    fake.MarketOrder = _Order
    fake.LimitOrder = _Order
    fake.StopOrder = _Order
    fake.util = MagicMock()
    sys.modules['ib_insync'] = fake

from live.broker_ib import IBBroker


def _make_broker():
    b = IBBroker()
    b.ib = MagicMock()
    seq = iter([100, 101, 102])
    b.ib.client.getReqId.side_effect = lambda: next(seq)
    b.contract = MagicMock()
    b.contract_id = 1
    b.tick_size = 0.25
    return b


def test_bracket_entry_returns_attached_with_all_ids():
    b = _make_broker()
    res = b.place_bracket_entry('short', 20, 30010.5, 30026.75, 29965.0)
    assert res == {'entry': 100, 'stop': 101, 'target': 102, 'attached': True}


def test_bracket_entry_submits_three_orders_atomically():
    b = _make_broker()
    b.place_bracket_entry('short', 20, 30010.5, 30026.75, 29965.0)
    # parent + stop + target submitted in one shot
    assert b.ib.placeOrder.call_count == 3
    parent, stop, target = [c.args[1] for c in b.ib.placeOrder.call_args_list]

    # Only the final child transmits — that sends the whole group together.
    assert parent.transmit is False
    assert stop.transmit is False
    assert target.transmit is True


def test_bracket_children_linked_to_parent_and_oca():
    b = _make_broker()
    b.place_bracket_entry('short', 20, 30010.5, 30026.75, 29965.0)
    parent, stop, target = [c.args[1] for c in b.ib.placeOrder.call_args_list]

    # Children only activate when the parent fills.
    assert stop.parentId == parent.orderId
    assert target.parentId == parent.orderId
    # OCA so a fill on one cancels the other.
    assert stop.ocaGroup == target.ocaGroup
    assert stop.ocaGroup


def test_bracket_exit_side_opposes_entry():
    b = _make_broker()
    b.place_bracket_entry('short', 20, 30010.5, 30026.75, 29965.0)
    parent, stop, target = [c.args[1] for c in b.ib.placeOrder.call_args_list]
    # Short entry -> SELL parent, BUY-to-cover children.
    assert parent.action == 'SELL'
    assert stop.action == 'BUY'
    assert target.action == 'BUY'

    b2 = _make_broker()
    b2.place_bracket_entry('long', 5, 20000.0, 19950.0, 20150.0)
    p2, s2, t2 = [c.args[1] for c in b2.ib.placeOrder.call_args_list]
    assert p2.action == 'BUY'
    assert s2.action == 'SELL'
    assert t2.action == 'SELL'
