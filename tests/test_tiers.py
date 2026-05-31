"""Tests for the account-tier presets and their wiring into the fleet."""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from config import Config
from tiers import apply_tier, TIERS, TIER_VALIDATION, VALID_TIERS, BASE_RISK


EXPECTED = {
    '25k': dict(dlc=500, dd=1500, target=1500, maxc=9, payout=2000),
    '50k': dict(dlc=1000, dd=2000, target=3000, maxc=9, payout=3000),
    '100k': dict(dlc=2000, dd=3000, target=6000, maxc=12, payout=5000),
    '150k': dict(dlc=3000, dd=4500, target=9000, maxc=150, payout=7500),
}


@pytest.mark.parametrize('tier', VALID_TIERS)
def test_apply_tier_sets_funded_rules(tier):
    cfg = apply_tier(Config(), tier)
    e = EXPECTED[tier]
    assert cfg.funded.dollar_loss_cap == e['dlc']
    assert cfg.funded.trailing_dd == e['dd']
    assert cfg.funded.static_threshold == e['dd']
    assert cfg.funded.eval_profit_target == e['target']
    assert cfg.funded.max_payout == e['payout']
    assert cfg.risk.max_contracts == e['maxc']
    # base risk applied for every tier
    assert cfg.funded.model_risk_dollars == BASE_RISK
    assert cfg.funded.model_risk_dollars is not BASE_RISK  # defensive copy


def test_tiers_are_monotonic_in_validated_extraction():
    months = [TIER_VALIDATION[t]['monthly_usd'] for t in ['25k', '50k', '100k', '150k']]
    assert months == sorted(months), f"tier monthly extraction not monotonic: {months}"


def test_all_validated_survival_at_least_90():
    for t in VALID_TIERS:
        assert TIER_VALIDATION[t]['survival_pct'] >= 90.0


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        apply_tier(Config(), '200k')


def test_tier_normalization():
    # accepts '$50K', '50K', '50_k' style
    assert apply_tier(Config(), '50K').funded.dollar_loss_cap == 1000
    assert apply_tier(Config(), '$100k').funded.dollar_loss_cap == 2000


def test_fleet_account_accepts_tier_and_build_argv_passes_it():
    from fleet_config import FleetAccount
    from run_fleet import build_argv
    acct = FleetAccount(name='a', broker='ib', tier='150k')
    argv = build_argv(acct, shadow_override=False)
    assert '--tier' in argv
    assert argv[argv.index('--tier') + 1] == '150k'

    acct_none = FleetAccount(name='b', broker='ib')
    assert '--tier' not in build_argv(acct_none, shadow_override=False)
