from bisect import bisect_left
from threading import Lock
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Set, Tuple, Union
from warnings import warn

from eth_typing import ChecksumAddress
from eth_utils.address import to_checksum_address
from web3.contract import Contract

from .. import config
from ..baseclasses import PoolHelper
from ..erc20_token import Erc20Token
from ..exceptions import (
    DeprecationError,
    ExternalUpdateError,
    LiquidityPoolError,
    NoPoolStateAvailable,
    ZeroSwapError,
)
from ..logging import logger
from ..manager import AllPools, Erc20TokenHelperManager
from .abi import CURVE_STABLESWAP_POOL_ABI


class CurveStableswapPool(PoolHelper):
    def __init__(
        self,
        address: Union[ChecksumAddress, str],
        tokens: Optional[List[Erc20Token]] = None,
        name: Optional[str] = None,
        update_method: str = "polling",
        abi: Optional[list] = None,
        factory_address: Optional[str] = None,
        # fee: Union[Fraction, Iterable[Fraction]] = Fraction(3, 1000),
        silent: bool = False,
        state_block: Optional[int] = None,
        empty: bool = False,
    ) -> None:
        """
        Create a new `CurveStableswapPool` object for interaction with a Curve
        Stablecoin pool.


        Arguments
        ---------
        address : str
            Address for the deployed pool contract.
        tokens : List[Erc20Token], optional
            "Erc20Token" objects for the tokens held by the deployed pool.
        name : str, optional
            Name of the contract, e.g. "DAI-WETH".
        update_method : str
            A string that sets the method used to fetch updates to the pool.
            Can be "polling", which fetches updates from the chain object
            using the contract object, or "external" which relies on updates
            being provided from outside the object.
        abi : list, optional
            Contract ABI.
        factory_address : str, optional
            The address for the factory contract. The default assumes a
            mainnet Curve Stableswap factory contract. If creating an
            object based on another forked ecosystem, provide this
            value or the address check will fail.
        fee : Fraction | (Fraction, Fraction)
            The swap fee imposed by the pool. Defaults to `Fraction(3,1000)`
            which is equivalent to 0.3%. For split-fee pools of unequal value,
            provide a tuple or list with the token0 fee in the first position,
            and the token1 fee in the second.
        silent : bool
            Suppress status output.
        state_block: int, optional
            Fetch initial state values from the chain at a particular block
            height. Defaults to the latest block if omitted.
        empty: bool
            Set to `True` to initialize the pool without initial values
            retrieved from chain, and skipping some validation. Useful for
            simulating transactions through pools that do not exist.
        """

        self._state_lock = Lock()

        self.address: ChecksumAddress = to_checksum_address(address)
        self.abi = abi if abi is not None else CURVE_STABLESWAP_POOL_ABI

        _w3 = config.get_web3()
        _w3_contract = self._w3_contract

        if factory_address:
            self.factory = to_checksum_address(factory_address)

        self._update_method = update_method

        if empty:
            self.update_block = 1
        else:
            self.update_block = (
                state_block if state_block is not None else _w3.eth.get_block_number()
            )
            self.factory = _w3_contract.functions.factory().call()

        chain_id = 1 if empty else _w3.eth.chain_id

        # if a token pair was provided, check and set pointers for token0 and token1
        if tokens is not None:
            if len(tokens) != 2:
                raise ValueError(f"Expected 2 tokens, found {len(tokens)}")
            self.token0 = min(tokens)
            self.token1 = max(tokens)
        else:
            _token_manager = Erc20TokenHelperManager(chain_id)
            self.token0 = _token_manager.get_erc20token(
                address=_w3_contract.functions.coins(0).call(),
                silent=silent,
            )
            self.token1 = _token_manager.get_erc20token(
                address=_w3_contract.functions.coins(1).call(),
                silent=silent,
            )

        self.reserves_token0: int = 0
        self.reserves_token1: int = 0

        if not empty:
            (
                self.reserves_token0,
                self.reserves_token1,
                *_,
            ) = _w3_contract.functions.get_balances().call(block_identifier=self.update_block)
