import dataclasses
from typing import List, Literal, Optional

from eth_typing import AnyAddress

from ..baseclasses import AbstractPoolUpdate


@dataclasses.dataclass(slots=True, frozen=True)
class CurveStableswapPoolState:
    pool: AnyAddress  # TODO: convert other states to reference address instead of object
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
    buyer: Optional[str] = dataclasses.field(default=None)
    tx: Optional[str] = dataclasses.field(compare=False, default=None)


@dataclasses.dataclass(slots=True, frozen=True)
class CurveStableSwapPoolAttributes:
    address: AnyAddress
    lp_token_address: AnyAddress
    coin_addresses: List[AnyAddress]
    coin_index_type: Literal["int128", "uint256"]
    fee: int
    admin_fee: int
    is_metapool: bool
    underlying_coin_addresses: Optional[List[AnyAddress]] = dataclasses.field(default=None)
    base_pool_address: Optional[AnyAddress] = dataclasses.field(default=None)
