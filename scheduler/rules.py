"""
Soft-rule scoring functions for charger priority.

Each function returns a non-negative float cost for a bus arriving at a station.
Lower cost = higher priority at the charger.

How to add a new rule:
1. Define a function with signature: (bus, context) -> float
2. Register it in RULE_REGISTRY
3. Add a corresponding key to the Weights dataclass in models.py
4. Update the score() function here to include the new term
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Bus, Weights


class ScoringContext:
    """
    Runtime context passed to each rule function.
    Contains all info a rule might need without coupling rules to the engine.
    """
    def __init__(
        self,
        bus: "Bus",
        arrival_min: float,
        accumulated_wait_min: float,        # total wait this bus has accrued so far
        operator_avg_wait: dict[str, float], # operator -> avg wait across their fleet so far
        global_avg_wait: float,              # avg wait across ALL buses so far
    ):
        self.bus = bus
        self.arrival_min = arrival_min
        self.accumulated_wait_min = accumulated_wait_min
        self.operator_avg_wait = operator_avg_wait
        self.global_avg_wait = global_avg_wait


def individual_cost(ctx: ScoringContext) -> float:
    """
    Penalizes buses that have already waited a lot — give priority to those least delayed.
    Lower accumulated wait → lower cost → higher priority.
    Returns wait in minutes (normalized scale).
    """
    return ctx.accumulated_wait_min


def operator_cost(ctx: ScoringContext) -> float:
    """
    Penalizes a bus whose operator is already ahead of the fleet average.
    Promotes fairness: if KPN buses are already running late, their cost is lower
    (higher priority) vs an operator that is on time.
    """
    op = ctx.bus.operator
    op_avg = ctx.operator_avg_wait.get(op, 0.0)
    global_avg = ctx.global_avg_wait
    # Higher operator avg delay → lower cost (need priority to catch up)
    return -(op_avg - global_avg)


def overall_cost(ctx: ScoringContext) -> float:
    """
    Minimizes total network time — prefer buses that arrived earliest (FCFS).
    Earlier arrival → lower cost → higher priority.
    """
    return ctx.arrival_min


def score(ctx: ScoringContext, weights: "Weights") -> float:
    """
    Composite score. Lower = higher priority in the charger queue.
    This is the single place where weights are applied.
    """
    return (
        weights.individual * individual_cost(ctx)
        + weights.operator * operator_cost(ctx)
        + weights.overall * overall_cost(ctx)
    )
