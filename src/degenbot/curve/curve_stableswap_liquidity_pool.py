from bisect import bisect_left
from fractions import Fraction
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
    EVMRevertError,
)
from ..logging import logger
from ..manager import Erc20TokenHelperManager
from ..registry import AllPools
from ..subscription_mixins import Subscriber, SubscriptionMixin
from .abi import CURVE_STABLESWAP_POOL_ABI
from .stableswap_dataclasses import CurveStableswapPoolSimulationResult, CurveStableswapPoolState


class CurveStableswapPool(SubscriptionMixin, PoolHelper):
    # Constants from contract
    # ref: https://github.com/curvefi/curve-contract/blob/master/contracts/pool-templates/base/SwapTemplateBase.vy
    PRECISION = 10**18
    FEE_DENOMINATOR = 10**10
    A_PRECISION = 100

    def __init__(
        self,
        address: Union[ChecksumAddress, str],
        tokens: Optional[List[Erc20Token]] = None,
        a_coefficient: Optional[int] = None,
        name: Optional[str] = None,
        update_method: str = "polling",
        abi: Optional[list] = None,
        factory_address: Optional[str] = None,
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

        self.fee: int = _w3_contract.functions.fee().call()

        self._update_method = update_method

        self.a_coefficient = (
            a_coefficient
            if a_coefficient is not None
            else _w3_contract.functions.A_precise().call()
        )

        if empty:
            self.update_block = 1
        else:
            self.update_block = (
                state_block if state_block is not None else _w3.eth.get_block_number()
            )
            self.factory = _w3_contract.functions.factory().call()

        chain_id = 1 if empty else _w3.eth.chain_id

        number_of_tokens: int = len(_w3_contract.functions.get_balances().call())
        token_addresses: List[ChecksumAddress] = [
            _w3_contract.functions.coins(coin_index).call()
            for coin_index in range(number_of_tokens)
        ]

        if tokens is not None:
            # Index the tokens by address
            sorted_tokens = {token.address: token for token in tokens}

            # Sort and store the tokens
            for token_address in token_addresses:
                if token_address not in sorted_tokens:
                    raise ValueError(f"Token {token_address} not found in tokens.")
            self.tokens = tuple([sorted_tokens[token_address] for token_address in token_addresses])

        else:
            _token_manager = Erc20TokenHelperManager(chain_id)
            self.tokens = tuple(
                [
                    _token_manager.get_erc20token(
                        address=token_address,
                        silent=silent,
                    )
                    for token_address in token_addresses
                ]
            )

        self.rate_multipliers = tuple([10**token.decimals for token in self.tokens])

        self.initial_A = _w3_contract.functions.initial_A().call()
        self.future_A = _w3_contract.functions.future_A().call()
        self.initial_A_time = _w3_contract.functions.initial_A_time().call()
        self.future_A_time = _w3_contract.functions.future_A_time().call()

        if name is not None:
            self.name = name
        else:
            fee_string = f"{100*self.fee.numerator/self.fee.denominator:.2f}"
            token_string = "-".join([token.symbol for token in self.tokens])
            self.name = f"{token_string} (CurveStable, {fee_string}%)"

        self.balances = [0] * number_of_tokens
        if not empty:
            self.balances = _w3_contract.functions.get_balances().call(
                block_identifier=self.update_block
            )

        self.state = CurveStableswapPoolState(
            pool=self,
            balances=self.balances,
        )
        self._pool_state_archive: Dict[int, CurveStableswapPoolState] = {
            0: CurveStableswapPoolState(pool=self, balances=self.balances),
            self.update_block: self.state,
        }

        AllPools(chain_id)[self.address] = self

        self._subscribers: Set[Subscriber] = set()

        if not silent:
            logger.info(self.name)
            for token, balance in zip(self.tokens, self.balances):
                logger.info(f"• Token 0: {token} - Reserves: {balance}")

    def __repr__(self):  # pragma: no cover
        return f"CurveStableswapPool(address={self.address}, token0={self.token0}, token1={self.token1}, fee={100*self.fee.numerator/self.fee.denominator:.2f}%, A={self.a_coefficient})"

    @property
    def _w3_contract(self) -> Contract:
        return config.get_web3().eth.contract(
            address=self.address,
            abi=self.abi,
        )

    def _get_dx(self, i: int, j: int, dy: int) -> int:
        """
        @notice Calculate the current input dx given output dy
        @dev Index values can be found via the `coins` public getter method
        @param i Index value for the coin to send
        @param j Index valie of the coin to recieve
        @param dy Amount of `j` being received after exchange
        @return Amount of `i` predicted
        """

        rates: Tuple[int] = self.rate_multipliers
        xp: List[int] = self._xp_mem(rates, self.balances)

        y: int = xp[j] - (dy * rates[j] // self.PRECISION + 1) * self.FEE_DENOMINATOR // (
            self.FEE_DENOMINATOR - self.fee
        )
        x: int = self.get_y(j, i, y, xp, 0, 0)
        return (x - xp[i]) * self.PRECISION // rates[i]

    def _get_dy(self, i: int, j: int, dx: int) -> int:
        """
        @notice Calculate the current output dy given input dx
        @dev Index values can be found via the `coins` public getter method
        @param i Index value for the coin to send
        @param j Index value of the coin to recieve
        @param dx Amount of `i` being exchanged
        @return Amount of `j` predicted
        """

        rates: Tuple[int] = self.rate_multipliers
        xp: List[int] = self._xp_mem(rates, self.balances)

        x = xp[i] + (dx * rates[i] // self.PRECISION)
        y = self.get_y(i, j, x, xp, 0, 0)
        dy = xp[j] - y - 1
        fee = self.fee * dy // self.FEE_DENOMINATOR

        return (dy - fee) * self.PRECISION // rates[j]

    def get_y(
        self,
        i: int,
        j: int,
        x: int,
        xp: Iterable[int],
        _amp: int,
        _D: int,
    ) -> int:
        """
        Calculate x[j] if one makes x[i] = x

        Done by solving quadratic equation iteratively.
        x_1**2 + x_1 * (sum' - (A*n**n - 1) * D / (A * n**n)) = D ** (n + 1) / (n ** (2 * n) * prod' * A)
        x_1**2 + b*x_1 = c

        x_1 = (x_1**2 + c) / (2*x_1 + b)
        """

        N_COINS = len(self.tokens)
        N_COINS_128 = N_COINS

        # x in the input is converted to the same price/precision

        assert i != j  # dev: same coin
        assert j >= 0  # dev: j below zero
        assert j < N_COINS_128  # dev: j above N_COINS

        # should be unreachable, but good for safety
        assert i >= 0
        assert i < N_COINS_128

        amp: int = _amp
        D: int = _D
        if _D == 0:
            amp = self._A()
            D = self.get_D(xp, amp)
        S_: int = 0
        _x: int = 0
        y_prev = 0
        c = D
        Ann = amp * N_COINS

        for _i in range(N_COINS_128):
            if _i == i:
                _x = x
            elif _i != j:
                _x = xp[_i]
            else:
                continue
            S_ += _x
            c = c * D // (_x * N_COINS)

        c = c * D * self.A_PRECISION // (Ann * N_COINS)
        b = S_ + D * self.A_PRECISION // Ann  # - D
        y = D

        for _i in range(255):
            y_prev = y
            y = (y * y + c) // (2 * y + b - D)
            # Equality with the precision of 1
            if y > y_prev:
                if y - y_prev <= 1:
                    return y
            else:
                if y_prev - y <= 1:
                    return y
        raise

    def _xp_mem(
        self,
        _rates: Iterable[int],
        _balances: Iterable[int],
    ) -> List[int]:
        return [rate * balance // self.PRECISION for rate, balance in zip(_rates, _balances)]

    def _A(
        self,
        timestamp: Optional[int] = None,
    ) -> int:
        """
        Handle ramping A up or down
        """

        if timestamp is None:
            timestamp = self.future_A_time + 1

        t1 = self.future_A_time
        A1 = self.future_A

        if (
            timestamp < t1
        ):  # <--- modified from contract template, takes timestamp instead of block.timestamp
            A0 = self.initial_A
            t0 = self.initial_A_time
            # Expressions in uint256 cannot have negative numbers, thus "if"
            if A1 > A0:
                return A0 + (A1 - A0) * (timestamp - t0) // (t1 - t0)
            else:
                return A0 - (A0 - A1) * (timestamp - t0) // (t1 - t0)

        else:  # when t1 == 0 or timestamp >= t1
            return A1

    def get_D(
        self,
        _xp: List[int],
        _amp: int,
    ) -> int:
        """
        D invariant calculation in non-overflowing integer operations
        iteratively

        A * sum(x_i) * n**n + D = A * D * n**n + D**(n+1) / (n**n * prod(x_i))

        Converging solution:
        D[j+1] = (A * n**n * sum(x_i) - D[j]**(n+1) / (n**n prod(x_i))) / (A * n**n - 1)
        """

        N_COINS = len(self.tokens)

        S = 0
        for x in _xp:
            S += x
        if S == 0:
            return 0

        D = S
        Ann: int = _amp * N_COINS
        for _ in range(255):
            D_P: int = D * D // _xp[0] * D // _xp[1] // N_COINS**N_COINS
            Dprev: int = D
            D = (
                (Ann * S // self.A_PRECISION + D_P * N_COINS)
                * D
                // ((Ann - self.A_PRECISION) * D // self.A_PRECISION + (N_COINS + 1) * D_P)
            )
            # Equality with the precision of 1
            if D > Dprev:
                if D - Dprev <= 1:
                    return D
            else:
                if Dprev - D <= 1:
                    return D
        # convergence typically occurs in 4 rounds or less, this should be unreachable!
        # if it does happen the pool is borked and LPs can withdraw via `remove_liquidity`
        raise EVMRevertError("get_D did not converge!")

    def calculate_tokens_out_from_tokens_in(
        self,
        token_in: Erc20Token,
        token_out: Erc20Token,
        token_in_quantity: int,
        override_state: Optional[CurveStableswapPoolState] = None,
    ) -> int:
        """
        Calculates the expected token OUTPUT for a target INPUT at current pool reserves.
        """

        if token_in_quantity <= 0:
            raise ZeroSwapError("token_in_quantity must be positive")

        if override_state:
            logger.debug("Overrides applied:")
            logger.debug(f"Balances: {override_state.balances}")

        return self._get_dy(
            i=self.tokens.index(token_in),
            j=self.tokens.index(token_out),
            dx=token_in_quantity,
        )

    def calculate_tokens_in_from_tokens_out(
        self,
        token_in: Erc20Token,
        token_out: Erc20Token,
        token_out_quantity: int,
        override_state: Optional[CurveStableswapPoolState] = None,
    ) -> int:
        """
        Calculates the expected token INPUT for a target OUT at current pool reserves.
        """

        if token_out_quantity <= 0:
            raise ZeroSwapError("token_out_quantity must be positive")

        if override_state:
            logger.debug("Overrides applied:")
            logger.debug(f"Balances: {override_state.balances}")

        return self._get_dx(
            i=self.tokens.index(token_in),
            j=self.tokens.index(token_out),
            dy=token_out_quantity,
        )
