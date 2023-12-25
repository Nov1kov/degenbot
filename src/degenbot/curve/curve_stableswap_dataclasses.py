import dataclasses
from typing import TYPE_CHECKING, List, Optional, Union
from eth_typing import ChecksumAddress
from ..baseclasses import AbstractPoolUpdate

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
class CurveStableswapPoolExternalUpdate(AbstractPoolUpdate):
    block_number: int = dataclasses.field(compare=False)
    sold_id: int
    bought_id: int
    tokens_sold: int
    tokens_bought: int
    buyer: Optional[Union[ChecksumAddress, str]] = dataclasses.field(default=None)
    tx: Optional[str] = dataclasses.field(compare=False, default=None)


@dataclasses.dataclass(slots=True, frozen=True)
class CurveStableSwapPoolAttributes:
    address: Union[ChecksumAddress, str]
    lp_token: Union[ChecksumAddress, str]
    coins: List[Union[ChecksumAddress, str]]
    coin_index_type: str
    fee: int
    admin_fee: int
    is_metapool: bool
    underlying_coins: Optional[List[Union[ChecksumAddress, str]]] = dataclasses.field(default=None)
    basepool: Optional[Union[ChecksumAddress, str]] = dataclasses.field(default=None)
