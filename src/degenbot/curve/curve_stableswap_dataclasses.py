import dataclasses
from typing import TYPE_CHECKING, List, Optional

from eth_typing import ChecksumAddress

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


@dataclasses.dataclass(slots=True, eq=False)
class CurveStableswapPoolExternalUpdate:
    block_number: int = dataclasses.field(compare=False)
    sold_id: int
    bought_id: int
    tokens_sold: int
    tokens_bought: int
    buyer: Optional[ChecksumAddress] = dataclasses.field(default=None)
    tx: Optional[str] = dataclasses.field(compare=False, default=None)
