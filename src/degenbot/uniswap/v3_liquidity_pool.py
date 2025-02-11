# TODO: add event prototype exporter method and handler for callbacks

import dataclasses
import warnings
from bisect import bisect_left
from decimal import Decimal
from threading import Lock
from typing import TYPE_CHECKING, Any, Dict, List, Set, Tuple

from eth_typing import ChecksumAddress
from eth_utils.address import to_checksum_address
from web3.contract.contract import Contract

from .. import config
from ..baseclasses import BaseLiquidityPool
from ..dex.uniswap import TICKLENS_ADDRESSES
from ..erc20_token import Erc20Token
from ..exceptions import (
    BitmapWordUnavailableError,
    BlockUnavailableError,
    EVMRevertError,
    ExternalUpdateError,
    LiquidityPoolError,
    NoPoolStateAvailable,
)
from ..logging import logger
from ..manager.token_manager import Erc20TokenHelperManager
from ..registry.all_pools import AllPools
from ..subscription_mixins import Subscriber, SubscriptionMixin
from .abi import UNISWAP_V3_POOL_ABI
from .v3_dataclasses import (
    UniswapV3BitmapAtWord,
    UniswapV3LiquidityAtTick,
    UniswapV3PoolExternalUpdate,
    UniswapV3PoolSimulationResult,
    UniswapV3PoolState,
)
from .v3_functions import generate_v3_pool_address
from .v3_libraries import LiquidityMath, SwapMath, TickBitmap, TickMath
from .v3_libraries.functions import to_int256
from .v3_tick_lens import TickLens


class V3LiquidityPool(SubscriptionMixin, BaseLiquidityPool):
    # Holds a reference to a TickLens contract object. This is a singleton
    # contract so there is no need to create separate references for each pool.
    # Dict is keyed by a tuple of chain ID and factory address
    _lens_contracts: Dict[Tuple[int, ChecksumAddress], TickLens] = dict()

    uniswap_version = 3

    _TICKSPACING_BY_FEE = {
        100: 1,
        500: 10,
        3000: 60,
        10000: 200,
    }

    def __init__(
        self,
        address: str,
        fee: int | None = None,
        lens: TickLens | None = None,
        tokens: List[Erc20Token] | None = None,
        name: str = "",
        update_method: str | None = None,
        abi: List[Any] | None = None,
        factory_address: str | None = None,
        factory_init_hash: str | None = None,
        extra_words: int = 10,
        silent: bool = False,
        tick_data: Dict[int, Dict[str, Any]] | None = None,
        tick_bitmap: Dict[int, Dict[str, Any]] | None = None,
        state_block: int | None = None,
    ):
        self.address = to_checksum_address(address)
        self.abi = abi if abi is not None else UNISWAP_V3_POOL_ABI

        _w3 = config.get_web3()
        _w3_contract = self._w3_contract

        self.state: UniswapV3PoolState = UniswapV3PoolState(
            pool=self,
            liquidity=0,
            sqrt_price_x96=0,
            tick=0,
            tick_bitmap=dict(),
            tick_data=dict(),
        )

        # held for operations that manipulate state data
        self._state_lock = Lock()

        self._update_block = state_block if state_block else _w3.eth.get_block_number()

        if factory_address:
            self.factory = to_checksum_address(factory_address)
        else:
            self.factory = to_checksum_address(_w3_contract.functions.factory().call())

        if lens:
            self.lens = lens
        else:
            # Use the singleton TickLens helper if available
            try:
                self.lens = self._lens_contracts[(_w3.eth.chain_id, self.factory)]
            except KeyError:
                self.lens = TickLens(address=TICKLENS_ADDRESSES[_w3.eth.chain_id][self.factory])
                self._lens_contracts[(_w3.eth.chain_id, self.factory)] = self.lens

        token0_address: ChecksumAddress = to_checksum_address(
            _w3_contract.functions.token0().call()
        )
        token1_address: ChecksumAddress = to_checksum_address(
            _w3_contract.functions.token1().call()
        )

        if tokens is not None:
            if len(tokens) != 2:
                raise ValueError(f"Expected exactly two tokens, found {len(tokens)}")

            self.token0 = min(tokens)
            self.token1 = max(tokens)

            if not (self.token0 == token0_address and self.token1 == token1_address):
                raise ValueError("Token addresses do not match tokens recorded at contract")
        else:
            _token_manager = Erc20TokenHelperManager(_w3.eth.chain_id)
            self.token0 = _token_manager.get_erc20token(
                address=token0_address,
                silent=silent,
            )
            self.token1 = _token_manager.get_erc20token(
                address=token1_address,
                silent=silent,
            )

        self.tokens = (self.token0, self.token1)

        self._fee: int = fee if fee is not None else _w3_contract.functions.fee().call()
        self._tick_spacing = self._TICKSPACING_BY_FEE[self._fee]  # immutable

        if factory_address is not None and factory_init_hash is not None:
            computed_pool_address = generate_v3_pool_address(
                token_addresses=[self.token0.address, self.token1.address],
                fee=self._fee,
                factory_address=factory_address,
                init_hash=factory_init_hash,
            )
            if computed_pool_address != self.address:
                raise ValueError(
                    f"Pool address {self.address} does not match deterministic address {computed_pool_address} from factory"
                )

        if name:  # pragma: no cover
            self.name = name
        else:
            self.name = f"{self.token0}-{self.token1} (V3, {self._fee/10000:.2f}%)"

        if update_method is not None:  # pragma: no cover
            warnings.warn(
                "The `update_method` argument to `V3LiquidityPool()` is unused and otherwise ignored. Remove it to stop seeing this message."
            )
            self._update_method = update_method
        self._extra_words = extra_words

        # default to an empty, sparse bitmap with no tick data
        self._sparse_bitmap = True

        if (tick_bitmap is not None) != (tick_data is not None):
            raise ValueError(
                f"Must provide both tick_bitmap and tick_data! Got {tick_bitmap=}, {tick_data=}"
            )

        if tick_bitmap is not None and tick_data is not None:
            # if a snapshot was provided, assume it is complete
            self._sparse_bitmap = False

        if tick_bitmap is not None:
            # transform dict to UniswapV3BitmapAtWord
            self.tick_bitmap = {
                int(word): (
                    UniswapV3BitmapAtWord(**bitmap_at_word)
                    if not isinstance(
                        bitmap_at_word,
                        UniswapV3BitmapAtWord,
                    )
                    else bitmap_at_word
                )
                for word, bitmap_at_word in tick_bitmap.items()
            }

        if tick_data is not None:
            # transform dict to LiquidityAtTick
            self.tick_data = {
                int(tick): (
                    UniswapV3LiquidityAtTick(**liquidity_at_tick)
                    if not isinstance(
                        liquidity_at_tick,
                        UniswapV3LiquidityAtTick,
                    )
                    else liquidity_at_tick
                )
                for tick, liquidity_at_tick in tick_data.items()
            }

        if tick_bitmap is None and tick_data is None:
            logger.debug(f"{self} @ {self.address} updating -> {tick_bitmap=}, {tick_data=}")
            word_position, _ = self._get_tick_bitmap_position(self.tick)

            self._update_tick_data_at_word(
                word_position,
                block_number=self._update_block,
            )

        self.liquidity = _w3_contract.functions.liquidity().call(
            block_identifier=self._update_block
        )

        (
            self.sqrt_price_x96,
            self.tick,
            *_,
        ) = _w3_contract.functions.slot0().call(block_identifier=self._update_block)

        self._pool_state_archive: Dict[int, UniswapV3PoolState] = {
            0: UniswapV3PoolState(
                pool=self,
                liquidity=0,
                sqrt_price_x96=0,
                tick=0,
            ),
            self._update_block: self.state,
        }

        AllPools(_w3.eth.chain_id)[self.address] = self

        self._subscribers: Set[Subscriber] = set()

        if not silent:
            logger.info(self.name)
            logger.info(f"• Token 0: {self.token0}")
            logger.info(f"• Token 1: {self.token1}")
            logger.info(f"• Fee: {self._fee}")
            logger.info(f"• Liquidity: {self.liquidity}")
            logger.info(f"• SqrtPrice: {self.sqrt_price_x96}")
            logger.info(f"• Tick: {self.tick}")

    def __getstate__(self) -> Dict[str, Any]:
        # Remove objects that cannot be pickled and are unnecessary to perform
        # the calculation
        copied_attributes = (
            "tick_bitmap",
            "tick_data",
        )

        dropped_attributes = (
            "_state_lock",
            "_subscribers",
            "lens",
        )

        with self._state_lock:
            return {
                k: (v.copy() if k in copied_attributes else v)
                for k, v in self.__dict__.items()
                if k not in dropped_attributes
            }

    def __repr__(self) -> str:  # pragma: no cover
        return f"V3LiquidityPool(address={self.address}, token0={self.token0}, token1={self.token1}, fee={self._fee})"

    def __str__(self) -> str:
        return self.name

    def _get_tick_bitmap_position(self, tick: int) -> Tuple[int, int]:
        """
        Retrieves the wordPosition and bitPosition for the input tick

        This function corrects internally for tick spacing! e.g. tick=600 is the
        11th initialized tick for an LP with tickSpacing of 60, starting at 0.
        Each "word" in the tickBitmap holds 256 initialized positions, so the 11th
        position of the 1st word will represent tick=600.

        Calling `get_tick_bitmap_position(600)` returns (0,10), where:
            0 = wordPosition (zero-indexed)
            10 = bitPosition (zero-indexed)
        """
        return TickBitmap.position(int(Decimal(tick) // self._tick_spacing))

    def _update_tick_data_at_word(
        self,
        word_position: int,
        block_number: int | None = None,
    ) -> None:
        """
        Update the initialized tick values at a specific word (a 32 byte number
        representing 256 ticks at the tickSpacing interval). Store
        the liquidity values in the `self.tick_data` dictionary using the tick
        as the key, and update the `self.tick_bitmap` dictionary.

        Uses a lock to guard state-modifying methods that might cause race conditions
        when used with threads.
        """

        if word_position in self.tick_bitmap:
            logger.debug(f"Short-circuiting update, word position {word_position} already known.")
            return

        _w3_contract = self._w3_contract

        if block_number is None:
            block_number = config.get_web3().eth.get_block_number()

        with self._state_lock:
            try:
                _tick_bitmap = _w3_contract.functions.tickBitmap(word_position).call(
                    block_identifier=block_number,
                )
                _tick_data = self.lens._w3_contract.functions.getPopulatedTicksInWord(
                    self.address, word_position
                ).call(block_identifier=block_number)
            except Exception as e:
                print(f"(V3LiquidityPool) (_update_tick_data_at_word) (single tick): {e}")
                print(type(e))
                raise
            else:
                self.tick_bitmap[word_position] = UniswapV3BitmapAtWord(
                    bitmap=_tick_bitmap,
                    block=block_number,
                )
                for tick, liquidity_net, liquidity_gross in _tick_data:
                    self.tick_data[tick] = UniswapV3LiquidityAtTick(
                        liquidityNet=liquidity_net,
                        liquidityGross=liquidity_gross,
                        block=block_number,
                    )

    def _uniswap_v3_pool_swap(
        self,
        zeroForOne: bool,
        amount_specified: int,
        sqrt_price_limit_x96: int,
        override_start_liquidity: int | None = None,
        override_start_sqrt_price_x96: int | None = None,
        override_start_tick: int | None = None,
        override_tick_data: Dict[int, Any] | None = None,
        override_tick_bitmap: Dict[int, Any] | None = None,
    ) -> Tuple[int, int, int, int, int]:
        """
        This function is ported and adapted from the UniswapV3Pool.sol contract
        at https://github.com/Uniswap/v3-core/blob/main/contracts/UniswapV3Pool.sol

        It is called by the `calculate_tokens_in_from_tokens_out` and `calculate_tokens_out_from_tokens_in` methods to calculate
        swap amounts, ticks crossed, liquidity changes at various ticks, etc.
        """

        @dataclasses.dataclass(slots=True, eq=False)
        class SwapCache:
            liquidity_start: int
            tick_cumulative: int

        @dataclasses.dataclass(slots=True, eq=False)
        class SwapState:
            amount_specified_remaining: int
            amount_calculated: int
            sqrt_price_x96: int
            tick: int
            liquidity: int

        @dataclasses.dataclass(slots=True, eq=False)
        class StepComputations:
            sqrt_price_start_x96: int = 0
            tick_next: int = 0
            initialized: bool = False
            sqrt_price_next_x96: int = 0
            amount_in: int = 0
            amount_out: int = 0
            fee_amount: int = 0

        if amount_specified == 0:
            raise EVMRevertError("AS")

        _liquidity = (
            override_start_liquidity if override_start_liquidity is not None else self.liquidity
        )
        _sqrt_price_x96 = (
            override_start_sqrt_price_x96
            if override_start_sqrt_price_x96 is not None
            else self.sqrt_price_x96
        )
        _tick = override_start_tick if override_start_tick is not None else self.tick
        _tick_bitmap = (
            override_tick_bitmap if override_tick_bitmap is not None else self.tick_bitmap
        )
        _tick_data = override_tick_data if override_tick_data is not None else self.tick_data

        if not (
            sqrt_price_limit_x96 < _sqrt_price_x96
            and sqrt_price_limit_x96 > TickMath.MIN_SQRT_RATIO
            if zeroForOne
            else sqrt_price_limit_x96 > _sqrt_price_x96
            and sqrt_price_limit_x96 < TickMath.MAX_SQRT_RATIO
        ):
            raise EVMRevertError("SPL")

        exactInput = amount_specified > 0
        cache = SwapCache(liquidity_start=_liquidity, tick_cumulative=0)
        state = SwapState(
            amount_specified_remaining=amount_specified,
            amount_calculated=0,
            sqrt_price_x96=_sqrt_price_x96,
            tick=_tick,
            liquidity=cache.liquidity_start,
        )

        while (
            state.amount_specified_remaining != 0 and state.sqrt_price_x96 != sqrt_price_limit_x96
        ):
            step = StepComputations()

            step.sqrt_price_start_x96 = state.sqrt_price_x96

            while True:
                try:
                    (
                        step.tick_next,
                        step.initialized,
                    ) = TickBitmap.nextInitializedTickWithinOneWord(
                        _tick_bitmap,
                        state.tick,
                        self._tick_spacing,
                        zeroForOne,
                    )
                except BitmapWordUnavailableError as e:
                    missing_word = e.args[1]
                    if self._sparse_bitmap:
                        logger.debug(f"(swap) {self.name} fetching word {missing_word}")
                        self._update_tick_data_at_word(word_position=missing_word)
                    else:
                        # bitmap is complete, so mark the word as empty
                        # self.tick_bitmap[missing_word] = UniswapV3BitmapAtWord()
                        _tick_bitmap[missing_word] = UniswapV3BitmapAtWord()
                else:
                    # nextInitializedTickWithinOneWord will search up to 256 ticks away, which may
                    # return a tick in an adjacent word if there are no initialized ticks in the current word.
                    # This word may not be known to the helper, so check and fetch the containing word for this tick
                    tick_next_word, _ = self._get_tick_bitmap_position(step.tick_next)

                    if self._sparse_bitmap and tick_next_word not in _tick_bitmap:
                        logger.debug(
                            f"tickNext={step.tick_next} out of range! Fetching word={tick_next_word}"
                            f"\n{self.name}"
                        )
                        self._update_tick_data_at_word(word_position=tick_next_word)
                    break

            # ensure that we do not overshoot the min/max tick, as the tick bitmap is not aware of these bounds
            if step.tick_next < TickMath.MIN_TICK:
                step.tick_next = TickMath.MIN_TICK
            elif step.tick_next > TickMath.MAX_TICK:
                step.tick_next = TickMath.MAX_TICK

            # get the price for the next tick
            step.sqrt_price_next_x96 = TickMath.getSqrtRatioAtTick(step.tick_next)

            # compute values to swap to the target tick, price limit, or point where input/output amount is exhausted
            (
                state.sqrt_price_x96,
                step.amount_in,
                step.amount_out,
                step.fee_amount,
            ) = SwapMath.computeSwapStep(
                state.sqrt_price_x96,
                sqrt_price_limit_x96
                if (
                    step.sqrt_price_next_x96 < sqrt_price_limit_x96
                    if zeroForOne
                    else step.sqrt_price_next_x96 > sqrt_price_limit_x96
                )
                else step.sqrt_price_next_x96,
                state.liquidity,
                state.amount_specified_remaining,
                self._fee,
            )

            if exactInput:
                state.amount_specified_remaining -= to_int256(step.amount_in + step.fee_amount)
                state.amount_calculated = to_int256(state.amount_calculated - step.amount_out)
            else:
                state.amount_specified_remaining += to_int256(step.amount_out)
                state.amount_calculated = to_int256(
                    state.amount_calculated + step.amount_in + step.fee_amount
                )

            # shift tick if we reached the next price
            if state.sqrt_price_x96 == step.sqrt_price_next_x96:
                # if the tick is initialized, run the tick transition
                if step.initialized:
                    tick_next = step.tick_next
                    liquidityNet = _tick_data[tick_next].liquidityNet

                    if zeroForOne:
                        liquidityNet = -liquidityNet

                    state.liquidity = LiquidityMath.addDelta(state.liquidity, liquidityNet)

                state.tick = step.tick_next - 1 if zeroForOne else step.tick_next

            elif state.sqrt_price_x96 != step.sqrt_price_start_x96:
                # recompute unless we're on a lower tick boundary (i.e. already transitioned ticks), and haven't moved
                state.tick = TickMath.getTickAtSqrtRatio(state.sqrt_price_x96)

        amount0, amount1 = (
            (
                amount_specified - state.amount_specified_remaining,
                state.amount_calculated,
            )
            if zeroForOne == exactInput
            else (
                state.amount_calculated,
                amount_specified - state.amount_specified_remaining,
            )
        )

        return (
            amount0,
            amount1,
            state.sqrt_price_x96,
            state.liquidity,
            state.tick,
        )

    @property
    def liquidity(self) -> int:
        return self.state.liquidity

    @liquidity.setter
    def liquidity(self, new_liquidity: int) -> None:
        self.state = UniswapV3PoolState(
            pool=self,
            liquidity=new_liquidity,
            sqrt_price_x96=self.sqrt_price_x96,
            tick=self.tick,
            tick_bitmap=self.tick_bitmap,
            tick_data=self.tick_data,
        )

    @property
    def sqrt_price_x96(self) -> int:
        return self.state.sqrt_price_x96

    @sqrt_price_x96.setter
    def sqrt_price_x96(self, new_sqrt_price_x96: int) -> None:
        self.state = UniswapV3PoolState(
            pool=self,
            liquidity=self.liquidity,
            sqrt_price_x96=new_sqrt_price_x96,
            tick=self.tick,
            tick_bitmap=self.tick_bitmap,
            tick_data=self.tick_data,
        )

    @property
    def tick(self) -> int:
        return self.state.tick

    @tick.setter
    def tick(self, new_tick: int) -> None:
        self.state = UniswapV3PoolState(
            pool=self,
            liquidity=self.liquidity,
            sqrt_price_x96=self.sqrt_price_x96,
            tick=new_tick,
            tick_bitmap=self.tick_bitmap,
            tick_data=self.tick_data,
        )

    @property
    def tick_bitmap(self) -> Dict[int, UniswapV3BitmapAtWord]:
        if TYPE_CHECKING:
            assert self.state.tick_bitmap is not None
        return self.state.tick_bitmap

    @tick_bitmap.setter
    def tick_bitmap(self, new_tick_bitmap: Dict[int, UniswapV3BitmapAtWord]) -> None:
        self.state = UniswapV3PoolState(
            pool=self,
            liquidity=self.liquidity,
            sqrt_price_x96=self.sqrt_price_x96,
            tick=self.tick,
            tick_bitmap=new_tick_bitmap,
            tick_data=self.tick_data,
        )

    @property
    def tick_data(self) -> Dict[int, UniswapV3LiquidityAtTick]:
        if TYPE_CHECKING:
            assert self.state.tick_data is not None
        return self.state.tick_data

    @tick_data.setter
    def tick_data(self, new_tick_data: Dict[int, UniswapV3LiquidityAtTick]) -> None:
        self.state = UniswapV3PoolState(
            pool=self,
            liquidity=self.liquidity,
            sqrt_price_x96=self.sqrt_price_x96,
            tick=self.tick,
            tick_bitmap=self.tick_bitmap,
            tick_data=new_tick_data,
        )

    @property
    def _w3_contract(self) -> Contract:
        return config.get_web3().eth.contract(
            address=self.address,
            abi=self.abi,
        )

    def auto_update(
        self,
        block_number: int | None = None,
        silent: bool = True,
    ) -> Tuple[bool, UniswapV3PoolState]:
        """
        Retrieves the current slot0 and liquidity values from the LP, stores any that have changed,
        and returns a tuple with a status boolean indicating whether any update was found,
        and a dictionary holding current state values:
            - liquidity
            - sqrt_price_x96
            - tick

        Uses a lock to guard state-modifying methods that might cause race conditions
        when used with threads.
        """

        _w3_contract = self._w3_contract

        with self._state_lock:
            updated = False

            # use the block_number if provided, otherwise pull from web3
            if block_number is None:
                block_number = config.get_web3().eth.get_block_number()

            if block_number < self._update_block:
                raise ExternalUpdateError(
                    f"Current state recorded at block {self._update_block}, received update for stale block {block_number}"
                )

            (
                _sqrt_price_x96,
                _tick,
                *_,
            ) = _w3_contract.functions.slot0().call(
                block_identifier=block_number,
            )
            _liquidity = _w3_contract.functions.liquidity().call(block_identifier=block_number)

            if self.sqrt_price_x96 != _sqrt_price_x96:
                updated = True
                self.sqrt_price_x96 = _sqrt_price_x96

            if self.tick != _tick:
                updated = True
                self.tick = _tick

            if self.liquidity != _liquidity:
                updated = True
                self.liquidity = _liquidity

            if updated:
                self._notify_subscribers()
                # WIP: maintain a dict of pool states by block to unwind updates that were removed by a re-org
                self._pool_state_archive[block_number] = self.state

            if not silent:
                logger.info(f"Liquidity: {self.liquidity}")
                logger.info(f"SqrtPriceX96: {self.sqrt_price_x96}")
                logger.info(f"Tick: {self.tick}")

            # WORKAROUND: update the block even if there are no state changes
            # pools were being repeatedly caught by "stale pool" checks
            self._update_block = block_number

        return updated, self.state

    def calculate_tokens_out_from_tokens_in(
        self,
        token_in: Erc20Token,
        token_in_quantity: int,
        override_state: UniswapV3PoolState | None = None,
    ) -> int:
        """
        This function implements the common degenbot interface `calculate_tokens_out_from_tokens_in`
        to calculate the number of tokens withdrawn (out) for a given number of tokens deposited (in).

        It is similar to calling quoteExactInputSingle using the quoter contract with arguments:
        `quoteExactInputSingle(
            tokenIn=token_in,
            tokenOut=[automatically determined by helper],
            fee=[automatically determined by helper],
            amountIn=token_in_quantity,
            sqrt_price_limitX96 = 0
        )` which returns the value `amountOut`

        Note that this wrapper function always assumes that the sqrt_price_limitx96 argument is unset,
        thus the swap calculation will continue until the target amount is satisfied, regardless of
        price impact.

        Accepts a dictionary of state values (`override_state`) to allow calculations beginning from an
        arbitrary starting point. This dictionary must have one or more of the following keys:
            - 'liquidity'
            - 'sqrt_price_x96'
            - 'tick'
            - 'tick_data'  (not yet implemented)
            - 'tick_bitmap'  (not yet implemented)
        """

        if token_in not in self.tokens:
            raise ValueError("token_in not found!")

        if override_state:
            logger.debug(f"V3 calc with overridden state: {override_state}")

        _is_zero_for_one = token_in == self.token0

        try:
            amount0_delta, amount1_delta, *_ = self._uniswap_v3_pool_swap(
                zeroForOne=_is_zero_for_one,
                amount_specified=token_in_quantity,
                sqrt_price_limit_x96=(
                    TickMath.MIN_SQRT_RATIO + 1 if _is_zero_for_one else TickMath.MAX_SQRT_RATIO - 1
                ),
                override_start_liquidity=(
                    override_state.liquidity if override_state is not None else None
                ),
                override_start_sqrt_price_x96=(
                    override_state.sqrt_price_x96 if override_state is not None else None
                ),
                override_start_tick=(override_state.tick if override_state is not None else None),
                override_tick_bitmap=(
                    override_state.tick_bitmap if override_state is not None else self.tick_bitmap
                ),
                override_tick_data=(
                    override_state.tick_data if override_state is not None else self.tick_data
                ),
            )
        except EVMRevertError as e:
            raise LiquidityPoolError(f"Simulated execution reverted: {e}") from e
        else:
            return -amount1_delta if _is_zero_for_one else -amount0_delta

    def calculate_tokens_in_from_tokens_out(
        self,
        token_out: Erc20Token,
        token_out_quantity: int,
        override_state: UniswapV3PoolState | None = None,
    ) -> int:
        """
        This function implements the common degenbot interface `calculate_tokens_in_from_tokens_out`
        to calculate the number of tokens deposited (in) for a given number of tokens withdrawn (out).

        It is similar to calling quoteExactOutputSingle using the quoter contract with arguments:
        `quoteExactOutputSingle(
            tokenIn=[automatically determined by helper],
            tokenOut=token_out,
            fee=[automatically determined by helper],
            amountOut=token_out_quantity,
            sqrt_price_limitX96 = 0
        )` which returns the value `amountIn`

        Note that this wrapper function always assumes that the sqrt_price_limitx96 argument is unset, thus the
        swap calculation will continue until the target amount is satisfied, regardless of price impact

        Accepts a dictionary of state values (`override_state`) to allow calculations beginning from an
        arbitrary starting point. This dictionary must have one or more of the following keys:
            - 'liquidity'
            - 'sqrt_price_x96'
            - 'tick'
            - 'tick_data'  (not yet implemented)
            - 'tick_bitmap'  (not yet implemented)
        """

        if token_out not in self.tokens:
            raise ValueError("token_out not found!")

        _is_zero_for_one = token_out == self.token1

        try:
            amount0_delta, amount1_delta, *_ = self._uniswap_v3_pool_swap(
                zeroForOne=_is_zero_for_one,
                amount_specified=-token_out_quantity,
                sqrt_price_limit_x96=(
                    TickMath.MIN_SQRT_RATIO + 1 if _is_zero_for_one else TickMath.MAX_SQRT_RATIO - 1
                ),
                override_start_liquidity=(
                    override_state.liquidity if override_state is not None else None
                ),
                override_start_sqrt_price_x96=(
                    override_state.sqrt_price_x96 if override_state is not None else None
                ),
                override_start_tick=(override_state.tick if override_state is not None else None),
                override_tick_bitmap=(
                    override_state.tick_bitmap if override_state is not None else self.tick_bitmap
                ),
                override_tick_data=(
                    override_state.tick_data if override_state is not None else self.tick_data
                ),
            )
        except EVMRevertError as e:
            raise LiquidityPoolError(f"Simulated execution reverted: {e}") from e
        else:
            return amount0_delta if _is_zero_for_one else amount1_delta

    def external_update(
        self,
        update: UniswapV3PoolExternalUpdate | None = None,
        updates: Dict[str, Any] | None = None,
        block_number: int | None = None,
        silent: bool = True,
        fetch_missing: bool | None = None,
        force: bool = False,  # added primarily to support liquidity bootstrapping without excessive refactoring
    ) -> bool:
        """
        Process a `UniswapV3PoolExternalUpdate` with one or more of the following update types:
            - `tick`: int
            - `liquidity`: int
            - `sqrt_price_x96`: int
            - `liquidity_change`: tuple of (liquidity_delta, lower_tick, upper_tick). The delta can be positive or negative to indicate added or removed liquidity.

        `block_number` is validated against the most recently recorded block prior to recording any changes. If `force=True`, the block check is skipped.

        If any update is processed, `self.state` and `self.update_block` are updated.

        Returns a bool indicating whether any updated state value was recorded.

        @dev This method uses a lock to guard state-modifying methods that might cause race conditions when used with threads.
        """

        def is_valid_update_block(block_number: int) -> bool:
            """
            Check if `block_number` is valid (matches or exceeds the last update block)

            The `force` argument to `external_update` will trigger this to always return True
            """
            return True if force else block_number >= self._update_block

        if fetch_missing is not None:
            raise DeprecationWarning(
                "The fetch_missing argument has been deprecated, to address this exception remove it from any calls to external_update"
            )

        # warnings.warn(
        #     "\n"
        #     + "The `updates` dict argument is deprecated and will be "
        #     + "removed in the future. It has been converted in-place to a "
        #     + "`UniswapV3PoolExternalUpdate` object. Pass this using the "
        #     + "`update=` argument to remove this warning."
        #     + "\n"
        #     + "For the values you've provided, pass this data using "
        #     + "the format: \n"
        #     + "update=UniswapV3PoolExternalUpdate(\n"
        #     + (
        #         f"    liquidity={val}\n"
        #         if (val := updates.get("liquidity")) is not None
        #         else ""
        #     )
        #     + (
        #         f"    sqrt_price_x96={val}\n"
        #         if (val := updates.get("sqrt_price_x96")) is not None
        #         else ""
        #     )
        #     + (
        #         f"    tick={val}\n"
        #         if (val := updates.get("tick")) is not None
        #         else ""
        #     )
        #     + ")",
        # )

        if TYPE_CHECKING:
            assert isinstance(update, UniswapV3PoolExternalUpdate)

        # If a block number was not provided, pull from web3
        if block_number is None:
            if update is not None:
                block_number = update.block_number
            else:
                block_number = config.get_web3().eth.get_block_number()
                logger.warning(
                    f"(V3LiquidityPool.external_update) block_number was not provided, using {block_number} from chain"
                )

        if not is_valid_update_block(block_number):
            raise ExternalUpdateError(
                f"Rejected update for block {block_number} in the past, current update block is {self._update_block}"
            )

        if updates and not update:
            update = UniswapV3PoolExternalUpdate(
                block_number=block_number,
                liquidity=updates.get("liquidity"),
                sqrt_price_x96=updates.get("sqrt_price_x96"),
                tick=updates.get("tick"),
                liquidity_change=updates.get("liquidity_change"),
            )

        with self._state_lock:
            updated_state = False

            for update_type in ("tick", "liquidity", "sqrt_price_x96"):
                if (update_value := getattr(update, update_type, None)) and update_value != getattr(
                    self, update_type
                ):
                    setattr(self, update_type, update_value)
                    updated_state = True

            if update.liquidity_change and update.liquidity_change[0] != 0:
                (
                    liquidity_delta,
                    lower_tick,
                    upper_tick,
                ) = update.liquidity_change

                updated_state = True

                # adjust in-range liquidity if current tick is within the position's range
                if lower_tick <= self.tick < upper_tick and not force:
                    liquidity_before = self.liquidity
                    self.liquidity += liquidity_delta
                    logger.debug(
                        f"Adjusting in-range liquidity, TX {update.tx}, tick range = [{lower_tick},{upper_tick}], current tick = {self.tick}, {self.address=}, previous liquidity = {liquidity_before}, liquidity change = {liquidity_delta}, current liquidity = {self.liquidity}"
                    )
                    assert (
                        self.liquidity >= 0
                    ), f"{self.address=}, {liquidity_before=}, {self.liquidity=}, {update=} {block_number=} {self.tick=}"

                for i, tick in enumerate([lower_tick, upper_tick]):
                    tick_word, _ = self._get_tick_bitmap_position(tick)

                    if tick_word not in self.tick_bitmap:
                        # the tick bitmap must be available for the word prior to flipping
                        # the initialized status of any tick

                        if self._sparse_bitmap:
                            logger.debug(
                                f"(external_update) {tick_word=} not found in tick_bitmap {self.tick_bitmap.keys()=}"
                            )
                            try:
                                self._update_tick_data_at_word(
                                    word_position=tick_word,
                                    # Fetch the word using the previous block as a known "good" state snapshot
                                    block_number=block_number - 1,
                                )
                            except ValueError as e:
                                raise BlockUnavailableError(
                                    f"Could not query chain at block {block_number - 1}"
                                ) from e
                        else:
                            # The bitmap is complete (sparse=False), so mark this word as empty
                            self.tick_bitmap[tick_word] = UniswapV3BitmapAtWord()

                    # Get the liquidity info for this tick
                    try:
                        tick_liquidity_net, tick_liquidity_gross = (
                            self.tick_data[tick].liquidityNet,
                            self.tick_data[tick].liquidityGross,
                        )
                    except (KeyError, AttributeError):
                        # if it doesn't exist, initialize the tick and set the current values to zero
                        tick_liquidity_net = 0
                        tick_liquidity_gross = 0
                        TickBitmap.flipTick(
                            self.tick_bitmap,
                            tick,
                            self._tick_spacing,
                            update_block=block_number,
                        )

                    # MINT: add liquidity at lower tick (i==0), subtract at upper tick (i==1)
                    # BURN: subtract liquidity at lower tick (i==0), add at upper tick (i==1)
                    # Same equation, but for BURN events the liquidity_delta value is negative
                    new_liquidity_net = (
                        tick_liquidity_net + liquidity_delta
                        if i == 0
                        else tick_liquidity_net - liquidity_delta
                    )
                    new_liquidity_gross = tick_liquidity_gross + liquidity_delta

                    # Delete entirely if there is no liquidity referencing this tick, then flip it in the bitmap
                    if new_liquidity_gross == 0:
                        del self.tick_data[tick]
                        TickBitmap.flipTick(
                            self.tick_bitmap,
                            tick,
                            self._tick_spacing,
                            update_block=block_number,
                        )
                    # otherwise record the new values
                    else:
                        self.tick_data[tick] = UniswapV3LiquidityAtTick(
                            liquidityNet=new_liquidity_net,
                            liquidityGross=new_liquidity_gross,
                            block=block_number,
                        )

            if not silent:
                logger.debug(f"Liquidity: {self.liquidity}")
                logger.debug(f"SqrtPriceX96: {self.sqrt_price_x96}")
                logger.debug(f"Tick: {self.tick}")
                logger.debug(
                    f"liquidity event: {liquidity_delta} in tick range [{lower_tick},{upper_tick}], pool: {self.name}"
                    "\n"
                    f"old liquidity: {tick_liquidity_net} net, {tick_liquidity_gross} gross"
                    "\n"
                    f"new liquidity: {new_liquidity_net} net, {new_liquidity_gross} gross"
                )
                logger.debug(f"update block: {block_number} (last={self._update_block})")

            if updated_state:
                self._pool_state_archive[block_number] = self.state
                self._notify_subscribers()

                if not force:
                    self._update_block = block_number

            return updated_state

    def restore_state_before_block(
        self,
        block: int,
    ) -> None:
        """
        Restore the last pool state recorded prior to a target block.

        Use this method to maintain consistent state data following a chain
        re-organization.
        """

        # Find the index for the most recent pool state PRIOR to the requested
        # block number.
        #
        # e.g. Calling restore_state_before_block(block=104) for a pool with
        # states at blocks 100, 101, 102, 103, 104. `bisect_left()` returns
        # block_index=3, since block 104 is at index=4. The state held at
        # index=3 is for block 103.
        # block_index = self._pool_state_archive.bisect_left(block)

        with self._state_lock:
            known_blocks = list(self._pool_state_archive.keys())
            block_index = bisect_left(known_blocks, block)

            if block_index == 0:
                raise NoPoolStateAvailable(f"No pool state known prior to block {block}")

            # The last known state already meets the criterion, so return early
            if block_index == len(known_blocks):
                return

            # Remove states at and after the specified block
            for block in known_blocks[block_index:]:
                del self._pool_state_archive[block]

            restored_block, restored_state = list(self._pool_state_archive.items())[-1]

            self.state = restored_state
            self._update_block = restored_block

        self._notify_subscribers()

    def simulate_swap(
        self,
        token_in: Erc20Token | None = None,
        token_in_quantity: int | None = None,
        token_out: Erc20Token | None = None,
        token_out_quantity: int | None = None,
        sqrt_price_limit: int | None = None,
        override_state: UniswapV3PoolState | None = None,
    ) -> UniswapV3PoolSimulationResult:
        """
        [TBD]
        """

        if token_in is not None and token_in not in self.tokens:
            raise ValueError("token_in is unknown!")
        if token_out is not None and token_out not in self.tokens:
            raise ValueError("token_out is unknown!")

        if token_in is None and token_out is None:
            raise ValueError("Neither token_in nor token_out were provided.")
        elif token_in is not None and token_out is not None:
            raise ValueError("Provide token_in or token_out, not both.")

        if token_in is not None and token_in_quantity is None:
            raise ValueError("token_in_quantity not provided.")
        if token_out is not None and token_out_quantity is None:
            raise ValueError("token_out_quantity not provided.")

        if 0 in (token_in_quantity, token_out_quantity):
            raise ValueError("Zero input/output swap requested.")

        # determine whether the swap is token0 -> token1
        if token_in is not None:
            zeroForOne = True if token_in == self.token0 else False
        elif token_out is not None:
            zeroForOne = True if token_out == self.token1 else False

        _sqrt_price_limit = (
            sqrt_price_limit
            if sqrt_price_limit is not None
            else (TickMath.MIN_SQRT_RATIO + 1 if zeroForOne else TickMath.MAX_SQRT_RATIO - 1)
        )

        if token_in_quantity is not None:
            _amount_specified = token_in_quantity
        elif token_out_quantity is not None:
            _amount_specified = -token_out_quantity

        try:
            (
                amount0_delta,
                amount1_delta,
                end_sqrtprice,
                end_liquidity,
                end_tick,
            ) = self._uniswap_v3_pool_swap(
                zeroForOne=zeroForOne,
                amount_specified=_amount_specified,
                sqrt_price_limit_x96=_sqrt_price_limit,
                override_start_liquidity=override_state.liquidity if override_state else None,
                override_start_sqrt_price_x96=override_state.sqrt_price_x96
                if override_state
                else None,
                override_start_tick=override_state.tick if override_state else None,
                override_tick_bitmap=override_state.tick_bitmap if override_state else None,
                override_tick_data=override_state.tick_data if override_state else None,
            )
        except EVMRevertError as e:
            raise LiquidityPoolError(f"Simulated execution reverted: {e}") from e
        else:
            return UniswapV3PoolSimulationResult(
                amount0_delta=amount0_delta,
                amount1_delta=amount1_delta,
                current_state=self.state.copy(),
                future_state=UniswapV3PoolState(
                    pool=self,
                    liquidity=end_liquidity,
                    sqrt_price_x96=end_sqrtprice,
                    tick=end_tick,
                ),
            )
