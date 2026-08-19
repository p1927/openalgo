"""
Strategy synthesis: given a user-drawn target payoff shape and a maximum
number of legs, searches for the option leg combination(s) that best match
that shape while favoring a good risk/reward profile.

Pure, dependency-free core (`payoff.py`, `objective.py`, `search.py`) that
only needs a candidate pool of (strike, option_type, premium) — fully
unit-testable without a broker connection. `service.py` is the thin
adapter that builds that candidate pool from OpenAlgo's live option chain.
"""

from .objective import ScoredCombo, score_combo
from .payoff import (
    LegCandidate,
    RiskProfile,
    SynthesizedLeg,
    combo_payoff,
    evaluate_risk,
    intrinsic_value,
    risk_reward_ratio,
)
from .probability import win_probability
from .search import synthesize
from .service import synthesize_from_option_chain

__all__ = [
    "LegCandidate",
    "RiskProfile",
    "ScoredCombo",
    "SynthesizedLeg",
    "combo_payoff",
    "evaluate_risk",
    "intrinsic_value",
    "risk_reward_ratio",
    "score_combo",
    "synthesize",
    "synthesize_from_option_chain",
    "win_probability",
]
