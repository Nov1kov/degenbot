import dataclasses
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .curve_stableswap_liquidity_pool import CurveStableswapPool


@dataclasses.dataclass(slots=True, frozen=True)
class CurveStableswapPoolState:
    pool: "CurveStableswapPool"
    balances: List[int]


@dataclasses.dataclass(slots=True, frozen=True)
class CurveStableswapPoolSimulationResult:
    amount0_delta: int
    amount1_delta: int
    current_state: CurveStableswapPoolState
    future_state: CurveStableswapPoolState
