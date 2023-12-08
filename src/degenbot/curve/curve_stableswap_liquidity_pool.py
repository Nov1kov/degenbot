from bisect import bisect_left
from fractions import Fraction
from threading import Lock
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Set, Tuple, Union
from warnings import warn

from eth_typing import ChecksumAddress
from eth_utils.address import to_checksum_address
from web3.contract import Contract

from .. import config
from ..constants import ZERO_ADDRESS
from ..baseclasses import PoolHelper
from ..erc20_token import Erc20Token
from ..exceptions import (
    ExternalUpdateError,
    LiquidityPoolError,
    NoPoolStateAvailable,
    ZeroSwapError,
    EVMRevertError,
    ZeroLiquidityError,
)
from ..logging import logger
from ..manager import Erc20TokenHelperManager
from ..registry import AllPools
from ..subscription_mixins import Subscriber, SubscriptionMixin
from .abi import CURVE_V1_POOL_ABI, CURVE_METAREGISTRY_ABI, CURVE_REGISTRY_ABI, CURVE_V1_FACTORY_ABI
from .curve_stableswap_dataclasses import (
    CurveStableswapPoolSimulationResult,
    CurveStableswapPoolState,
)
from hexbytes import HexBytes
from web3 import Web3

CURVE_REGISTRY_ADDRESS = "0x90E00ACe148ca3b23Ac1bC8C240C2a7Dd9c2d7f5"
CURVE_METAREGISTRY_ADDRESS = "0xF98B45FA17DE75FB1aD0e7aFD971b0ca00e379fC"
CURVE_V1_FACTORY_ADDRESS = "0x127db66E7F0b16470Bec194d0f496F9Fa065d0A9"

BROKEN_POOLS = (
    "0x1F71f05CF491595652378Fe94B7820344A551B8E",
    "0xD652c40fBb3f06d6B58Cb9aa9CFF063eE63d465D",
    "0x28B0Cf1baFB707F2c6826d10caf6DD901a6540C5",
    "0x84997FAFC913f1613F51Bb0E2b5854222900514B",
)


class BrokenPool(LiquidityPoolError):
    ...


class CurveStableswapPool(SubscriptionMixin, PoolHelper):
    # Constants from contract
    # ref: https://github.com/curvefi/curve-contract/blob/master/contracts/pool-templates/base/SwapTemplateBase.vy
    PRECISION = 10**18
    PRECISION_DECIMALS = 18
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

        if address.lower() in [pool.lower() for pool in BROKEN_POOLS]:
            raise BrokenPool

        self._state_lock = Lock()

        self.address: ChecksumAddress = to_checksum_address(address)
        self.abi = abi if abi is not None else CURVE_V1_POOL_ABI

        _w3 = config.get_web3()
        _w3_contract = self._w3_contract

        if self.address == "0x59Ab5a5b5d617E478a2479B0cAD80DA7e2831492":
            self.oracle_method = int.from_bytes(
                _w3.eth.call(
                    {
                        "to": self.address,
                        "data": HexBytes(Web3.keccak(text="oracle_method()"))[:4],
                    }
                ),
                byteorder="big",
            )

        # _w3_factory_contract: Contract = _w3.eth.contract(
        #     address=CURVE_V1_FACTORY_ADDRESS, abi=CURVE_V1_FACTORY_ABI
        # )
        # _w3_registry_contract: Contract = _w3.eth.contract(
        #     address=CURVE_REGISTRY_ADDRESS, abi=CURVE_REGISTRY_ABI
        # )

        _w3_metaregistry_contract = self.metaregistry

        self.lp_token = Erc20Token(
            _w3_metaregistry_contract.functions.get_lp_token(self.address).call()
        )

        self.is_metapool = _w3_metaregistry_contract.functions.is_meta(self.address).call()
        if self.is_metapool:
            self.base_pool = CurveStableswapPool(
                _w3_metaregistry_contract.functions.get_base_pool(self.address).call()
            )

        self.fee, *_ = _w3_metaregistry_contract.functions.get_fees(self.address).call()
        self._update_method = update_method

        pool_params = _w3_metaregistry_contract.functions.get_pool_params(self.address).call()
        if any(pool_params[1:]):
            raise TypeError(f"Pool {self.address} is not a StableSwap Curve pool.")

        self.a_coefficient, *_ = pool_params
        self.future_a_coefficient: Optional[int] = None
        self.future_a_coefficient_time: Optional[int] = None

        try:
            self.future_a_coefficient = _w3_contract.functions._future_A(self.address).call()
            self.future_a_coefficient_time = _w3_contract.functions._future_A_time(
                self.address
            ).call()
        except Exception:
            pass

        if empty:
            self.update_block = 1
        else:
            self.update_block = (
                state_block if state_block is not None else _w3.eth.get_block_number()
            )

        chain_id = 1 if empty else _w3.eth.chain_id

        token_addresses: List[ChecksumAddress] = [
            token_address
            for token_address in _w3_metaregistry_contract.functions.get_coins(self.address).call()
            if token_address != ZERO_ADDRESS
        ]

        if "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE" in token_addresses:
            print(
                f"ETH placeholder found at token position {token_addresses.index('0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE')}"
            )

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

        # TODO: create base pool helper separately, store LP token balance
        self.balances: List[int] = []
        self.underlying_balances: Optional[List[int]] = None

        if not empty:
            self.balances = _w3_metaregistry_contract.functions.get_balances(self.address).call()[
                : len(self.tokens)
            ]
            if self.is_metapool:
                self.underlying_balances = (
                    _w3_metaregistry_contract.functions.get_underlying_balances(
                        self.address
                    ).call()[: len(self.tokens)]
                )

        # For 3pool:
        # RATES = [
        #   1000000000000000000,             <------ 10**18 == 10**(18 + 18 - 18)
        #   1000000000000000000000000000000, <------ 10**30 == 10**(18 + 18 - 6)
        #   1000000000000000000000000000000, <------ 10**30 == 10**(18 + 18 - 6)
        # ]

        self.rates = tuple(
            [10 ** (2 * self.PRECISION_DECIMALS - token.decimals) for token in self.tokens]
        )

        if name is not None:  # pragma: no cover
            self.name = name
        else:
            fee_string = f"{100*self.fee/self.FEE_DENOMINATOR:.2f}"
            token_string = "-".join([token.symbol for token in self.tokens])
            self.name = f"{token_string} (CurveStable, {fee_string}%)"

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
            logger.info(
                f"{self.name} @ {self.address}, A={self.a_coefficient}, fee={100*self.fee/self.FEE_DENOMINATOR:.2f}%"
            )
            for token_id, (token, balance) in enumerate(zip(self.tokens, self.balances)):
                logger.info(f"• Token {token_id}: {token} - Reserves: {balance}")

    def __repr__(self):  # pragma: no cover
        token_string = "-".join([token.symbol for token in self.tokens])
        return f"CurveStableswapPool(address={self.address}, tokens={token_string}, fee={100*self.fee/self.FEE_DENOMINATOR:.2f}%, A={self.a_coefficient})"

    @property
    def _w3_contract(self) -> Contract:
        return config.get_web3().eth.contract(
            address=self.address,
            abi=self.abi,
        )

    def _get_dy(self, i: int, j: int, dx: int, dynamic_fee: bool = False) -> int:
        """
        @notice Calculate the current output dy given input dx
        @dev Index values can be found via the `coins` public getter method
        @param i Index value for the coin to send
        @param j Index value of the coin to recieve
        @param dx Amount of `i` being exchanged
        @return Amount of `j` predicted
        """

        # ref: https://github.com/curveresearch/notes/blob/main/stableswap.pdf

        def _dynamic_fee(xpi: int, xpj: int, _fee: int, _feemul: int) -> int:
            # dynamic fee pools:
            # 0xDeBF20617708857ebe4F679508E7b7863a8A8EeE
            if _feemul <= self.FEE_DENOMINATOR:
                return _fee
            else:
                xps2 = (xpi + xpj) ** 2
                return (_feemul * _fee) // (
                    (_feemul - self.FEE_DENOMINATOR) * 4 * xpi * xpj // xps2 + self.FEE_DENOMINATOR
                )

        if self.address in ("0x3Fb78e61784C9c637D560eDE23Ad57CA1294c14a",):
            rates = self.rates
            live_balances = [token.get_balance(self.address) for token in self.tokens]
            admin_balances = self.metaregistry.functions.get_admin_balances(self.address).call()[
                : len(self.tokens)
            ]
            balances = [
                pool_balance - admin_balance
                for pool_balance, admin_balance in zip(live_balances, admin_balances)
            ]
            xp = self._xp_mem(rates, balances)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y_with_A_precision(i, j, x, xp)
            dy = xp[j] - y - 1
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return (dy - fee) * self.PRECISION // rates[j]

        # TODO: investigate off-by-one compared to basic calc
        elif self.address in (
            "0x6A274dE3e2462c7614702474D64d376729831dCa",
            "0x3CFAa1596777CAD9f5004F9a0c443d912E262243",
            "0xb9446c4Ef5EBE66268dA6700D26f96273DE3d571",
            "0xe7A3b38c39F97E977723bd1239C3470702568e7B",
            "0xD7C10449A6D134A9ed37e2922F8474EAc6E5c100",
            "0xBa3436Fd341F2C8A928452Db3C5A3670d1d5Cc73",
            "0xfC8c34a3B3CFE1F1Dd6DBCCEC4BC5d3103b80FF0",
            "0x4424b4A37ba0088D8a718b8fc2aB7952C7e695F5",
        ):
            rates = self.rates
            xp = self._xp_mem(rates, self.balances)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y(i, j, x, xp)
            dy = xp[j] - y - 1
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return (dy - fee) * self.PRECISION // rates[j]

        elif self.address in ("0x48fF31bBbD8Ab553Ebe7cBD84e1eA3dBa8f54957",):
            xp = self.balances
            x = xp[i] + dx
            y = self._get_y_with_A_precision(i, j, x, xp)
            dy = xp[j] - y - 1
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return dy - fee

        elif self.address in ("0x59Ab5a5b5d617E478a2479B0cAD80DA7e2831492",):
            rates = self._stored_rates()  # TODO: write this oracle lookup

            live_balances = [token.get_balance(self.address) for token in self.tokens]
            admin_balances = self.metaregistry.functions.get_admin_balances(self.address).call()[
                : len(self.tokens)
            ]
            balances = [
                pool_balance - admin_balance
                for pool_balance, admin_balance in zip(live_balances, admin_balances)
            ]
            xp = self._xp_mem(rates, self.balances)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y_with_A_precision(i, j, x, xp)
            dy = xp[j] - y - 1
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return (dy - fee) * self.PRECISION // rates[j]

        elif self.is_metapool:
            if self.address in ("0xC61557C5d177bd7DC889A3b621eEC333e168f68A",):
                _rates = [
                    10**self.PRECISION_DECIMALS,
                    self.base_pool._w3_contract.functions.get_virtual_price().call(),
                ]
                xp = self._xp_mem(rates=_rates)
                x = xp[i] + (dx * _rates[i] // self.PRECISION)
                y = self._get_y_with_A_precision(i, j, x, xp)
            else:
                _rates = [
                    self.rates[0],
                    self.base_pool._w3_contract.functions.get_virtual_price().call(),
                ]
                xp = self._xp_mem(rates=_rates)
                x = xp[i] + (dx * _rates[i] // self.PRECISION)
                y = self._get_y(i, j, x, xp)

            dy = xp[j] - y - 1
            _fee = self.fee * dy // self.FEE_DENOMINATOR
            return (dy - _fee) * self.PRECISION // _rates[j]

        # dx and dy in c-units
        rates = self.rates
        xp = self._xp_mem()

        x = xp[i] + (dx * rates[i] // self.PRECISION)
        y = self._get_y(i, j, x, xp)
        dy = (xp[j] - y - 1) * self.PRECISION // rates[j]

        if dynamic_fee:
            dy = (xp[j] - y) * self.PRECISION // rates[j]
            import ujson

            offpeg_fee_multiplier = (
                self._w3_contract.w3.eth.contract(
                    address=self.address,
                    abi=ujson.loads(
                        """
                [{"name":"TokenExchange","inputs":[{"type":"address","name":"buyer","indexed":true},{"type":"int128","name":"sold_id","indexed":false},{"type":"uint256","name":"tokens_sold","indexed":false},{"type":"int128","name":"bought_id","indexed":false},{"type":"uint256","name":"tokens_bought","indexed":false}],"anonymous":false,"type":"event"},{"name":"TokenExchangeUnderlying","inputs":[{"type":"address","name":"buyer","indexed":true},{"type":"int128","name":"sold_id","indexed":false},{"type":"uint256","name":"tokens_sold","indexed":false},{"type":"int128","name":"bought_id","indexed":false},{"type":"uint256","name":"tokens_bought","indexed":false}],"anonymous":false,"type":"event"},{"name":"AddLiquidity","inputs":[{"type":"address","name":"provider","indexed":true},{"type":"uint256[3]","name":"token_amounts","indexed":false},{"type":"uint256[3]","name":"fees","indexed":false},{"type":"uint256","name":"invariant","indexed":false},{"type":"uint256","name":"token_supply","indexed":false}],"anonymous":false,"type":"event"},{"name":"RemoveLiquidity","inputs":[{"type":"address","name":"provider","indexed":true},{"type":"uint256[3]","name":"token_amounts","indexed":false},{"type":"uint256[3]","name":"fees","indexed":false},{"type":"uint256","name":"token_supply","indexed":false}],"anonymous":false,"type":"event"},{"name":"RemoveLiquidityOne","inputs":[{"type":"address","name":"provider","indexed":true},{"type":"uint256","name":"token_amount","indexed":false},{"type":"uint256","name":"coin_amount","indexed":false}],"anonymous":false,"type":"event"},{"name":"RemoveLiquidityImbalance","inputs":[{"type":"address","name":"provider","indexed":true},{"type":"uint256[3]","name":"token_amounts","indexed":false},{"type":"uint256[3]","name":"fees","indexed":false},{"type":"uint256","name":"invariant","indexed":false},{"type":"uint256","name":"token_supply","indexed":false}],"anonymous":false,"type":"event"},{"name":"CommitNewAdmin","inputs":[{"type":"uint256","name":"deadline","indexed":true},{"type":"address","name":"admin","indexed":true}],"anonymous":false,"type":"event"},{"name":"NewAdmin","inputs":[{"type":"address","name":"admin","indexed":true}],"anonymous":false,"type":"event"},{"name":"CommitNewFee","inputs":[{"type":"uint256","name":"deadline","indexed":true},{"type":"uint256","name":"fee","indexed":false},{"type":"uint256","name":"admin_fee","indexed":false},{"type":"uint256","name":"offpeg_fee_multiplier","indexed":false}],"anonymous":false,"type":"event"},{"name":"NewFee","inputs":[{"type":"uint256","name":"fee","indexed":false},{"type":"uint256","name":"admin_fee","indexed":false},{"type":"uint256","name":"offpeg_fee_multiplier","indexed":false}],"anonymous":false,"type":"event"},{"name":"RampA","inputs":[{"type":"uint256","name":"old_A","indexed":false},{"type":"uint256","name":"new_A","indexed":false},{"type":"uint256","name":"initial_time","indexed":false},{"type":"uint256","name":"future_time","indexed":false}],"anonymous":false,"type":"event"},{"name":"StopRampA","inputs":[{"type":"uint256","name":"A","indexed":false},{"type":"uint256","name":"t","indexed":false}],"anonymous":false,"type":"event"},{"outputs":[],"inputs":[{"type":"address[3]","name":"_coins"},{"type":"address[3]","name":"_underlying_coins"},{"type":"address","name":"_pool_token"},{"type":"address","name":"_aave_lending_pool"},{"type":"uint256","name":"_A"},{"type":"uint256","name":"_fee"},{"type":"uint256","name":"_admin_fee"},{"type":"uint256","name":"_offpeg_fee_multiplier"}],"stateMutability":"nonpayable","type":"constructor"},{"name":"A","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":5199},{"name":"A_precise","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":5161},{"name":"dynamic_fee","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"}],"stateMutability":"view","type":"function","gas":10278},{"name":"balances","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"i"}],"stateMutability":"view","type":"function","gas":2731},{"name":"get_virtual_price","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2680120},{"name":"calc_token_amount","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"bool","name":"is_deposit"}],"stateMutability":"view","type":"function","gas":5346581},{"name":"add_liquidity","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"uint256","name":"_min_mint_amount"}],"stateMutability":"nonpayable","type":"function"},{"name":"add_liquidity","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"uint256","name":"_min_mint_amount"},{"type":"bool","name":"_use_underlying"}],"stateMutability":"nonpayable","type":"function"},{"name":"get_dy","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"},{"type":"uint256","name":"dx"}],"stateMutability":"view","type":"function","gas":6239547},{"name":"get_dy_underlying","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"},{"type":"uint256","name":"dx"}],"stateMutability":"view","type":"function","gas":6239577},{"name":"exchange","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"},{"type":"uint256","name":"dx"},{"type":"uint256","name":"min_dy"}],"stateMutability":"nonpayable","type":"function","gas":6361682},{"name":"exchange_underlying","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"},{"type":"uint256","name":"dx"},{"type":"uint256","name":"min_dy"}],"stateMutability":"nonpayable","type":"function","gas":6369753},{"name":"remove_liquidity","outputs":[{"type":"uint256[3]","name":""}],"inputs":[{"type":"uint256","name":"_amount"},{"type":"uint256[3]","name":"_min_amounts"}],"stateMutability":"nonpayable","type":"function"},{"name":"remove_liquidity","outputs":[{"type":"uint256[3]","name":""}],"inputs":[{"type":"uint256","name":"_amount"},{"type":"uint256[3]","name":"_min_amounts"},{"type":"bool","name":"_use_underlying"}],"stateMutability":"nonpayable","type":"function"},{"name":"remove_liquidity_imbalance","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"uint256","name":"_max_burn_amount"}],"stateMutability":"nonpayable","type":"function"},{"name":"remove_liquidity_imbalance","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"uint256","name":"_max_burn_amount"},{"type":"bool","name":"_use_underlying"}],"stateMutability":"nonpayable","type":"function"},{"name":"calc_withdraw_one_coin","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"_token_amount"},{"type":"int128","name":"i"}],"stateMutability":"view","type":"function","gas":4449067},{"name":"remove_liquidity_one_coin","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"_token_amount"},{"type":"int128","name":"i"},{"type":"uint256","name":"_min_amount"}],"stateMutability":"nonpayable","type":"function"},{"name":"remove_liquidity_one_coin","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"_token_amount"},{"type":"int128","name":"i"},{"type":"uint256","name":"_min_amount"},{"type":"bool","name":"_use_underlying"}],"stateMutability":"nonpayable","type":"function"},{"name":"ramp_A","outputs":[],"inputs":[{"type":"uint256","name":"_future_A"},{"type":"uint256","name":"_future_time"}],"stateMutability":"nonpayable","type":"function","gas":151954},{"name":"stop_ramp_A","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":148715},{"name":"commit_new_fee","outputs":[],"inputs":[{"type":"uint256","name":"new_fee"},{"type":"uint256","name":"new_admin_fee"},{"type":"uint256","name":"new_offpeg_fee_multiplier"}],"stateMutability":"nonpayable","type":"function","gas":146482},{"name":"apply_new_fee","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":133744},{"name":"revert_new_parameters","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":21985},{"name":"commit_transfer_ownership","outputs":[],"inputs":[{"type":"address","name":"_owner"}],"stateMutability":"nonpayable","type":"function","gas":74723},{"name":"apply_transfer_ownership","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":60800},{"name":"revert_transfer_ownership","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":22075},{"name":"withdraw_admin_fees","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":71651},{"name":"donate_admin_fees","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":62276},{"name":"kill_me","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":38058},{"name":"unkill_me","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":22195},{"name":"set_aave_referral","outputs":[],"inputs":[{"type":"uint256","name":"referral_code"}],"stateMutability":"nonpayable","type":"function","gas":37325},{"name":"coins","outputs":[{"type":"address","name":""}],"inputs":[{"type":"uint256","name":"arg0"}],"stateMutability":"view","type":"function","gas":2310},{"name":"underlying_coins","outputs":[{"type":"address","name":""}],"inputs":[{"type":"uint256","name":"arg0"}],"stateMutability":"view","type":"function","gas":2340},{"name":"admin_balances","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"arg0"}],"stateMutability":"view","type":"function","gas":2370},{"name":"fee","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2291},{"name":"offpeg_fee_multiplier","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2321},{"name":"admin_fee","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2351},{"name":"owner","outputs":[{"type":"address","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2381},{"name":"lp_token","outputs":[{"type":"address","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2411},{"name":"initial_A","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2441},{"name":"future_A","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2471},{"name":"initial_A_time","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2501},{"name":"future_A_time","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2531},{"name":"admin_actions_deadline","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2561},{"name":"transfer_ownership_deadline","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2591},{"name":"future_fee","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2621},{"name":"future_admin_fee","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2651},{"name":"future_offpeg_fee_multiplier","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2681},{"name":"future_owner","outputs":[{"type":"address","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2711}]
                """
                    ),
                )
                .functions.offpeg_fee_multiplier()
                .call()
            )
            print(f"{offpeg_fee_multiplier=}")
            dyn_fee = _dynamic_fee(
                (xp[i] + x) // 2, (xp[j] + y) // 2, self.fee, offpeg_fee_multiplier
            )
            _fee = dyn_fee * dy // self.FEE_DENOMINATOR

            contract_fee = (
                self._w3_contract.w3.eth.contract(
                    address=self.address,
                    abi=ujson.loads(
                        """
                [{"name":"TokenExchange","inputs":[{"type":"address","name":"buyer","indexed":true},{"type":"int128","name":"sold_id","indexed":false},{"type":"uint256","name":"tokens_sold","indexed":false},{"type":"int128","name":"bought_id","indexed":false},{"type":"uint256","name":"tokens_bought","indexed":false}],"anonymous":false,"type":"event"},{"name":"TokenExchangeUnderlying","inputs":[{"type":"address","name":"buyer","indexed":true},{"type":"int128","name":"sold_id","indexed":false},{"type":"uint256","name":"tokens_sold","indexed":false},{"type":"int128","name":"bought_id","indexed":false},{"type":"uint256","name":"tokens_bought","indexed":false}],"anonymous":false,"type":"event"},{"name":"AddLiquidity","inputs":[{"type":"address","name":"provider","indexed":true},{"type":"uint256[3]","name":"token_amounts","indexed":false},{"type":"uint256[3]","name":"fees","indexed":false},{"type":"uint256","name":"invariant","indexed":false},{"type":"uint256","name":"token_supply","indexed":false}],"anonymous":false,"type":"event"},{"name":"RemoveLiquidity","inputs":[{"type":"address","name":"provider","indexed":true},{"type":"uint256[3]","name":"token_amounts","indexed":false},{"type":"uint256[3]","name":"fees","indexed":false},{"type":"uint256","name":"token_supply","indexed":false}],"anonymous":false,"type":"event"},{"name":"RemoveLiquidityOne","inputs":[{"type":"address","name":"provider","indexed":true},{"type":"uint256","name":"token_amount","indexed":false},{"type":"uint256","name":"coin_amount","indexed":false}],"anonymous":false,"type":"event"},{"name":"RemoveLiquidityImbalance","inputs":[{"type":"address","name":"provider","indexed":true},{"type":"uint256[3]","name":"token_amounts","indexed":false},{"type":"uint256[3]","name":"fees","indexed":false},{"type":"uint256","name":"invariant","indexed":false},{"type":"uint256","name":"token_supply","indexed":false}],"anonymous":false,"type":"event"},{"name":"CommitNewAdmin","inputs":[{"type":"uint256","name":"deadline","indexed":true},{"type":"address","name":"admin","indexed":true}],"anonymous":false,"type":"event"},{"name":"NewAdmin","inputs":[{"type":"address","name":"admin","indexed":true}],"anonymous":false,"type":"event"},{"name":"CommitNewFee","inputs":[{"type":"uint256","name":"deadline","indexed":true},{"type":"uint256","name":"fee","indexed":false},{"type":"uint256","name":"admin_fee","indexed":false},{"type":"uint256","name":"offpeg_fee_multiplier","indexed":false}],"anonymous":false,"type":"event"},{"name":"NewFee","inputs":[{"type":"uint256","name":"fee","indexed":false},{"type":"uint256","name":"admin_fee","indexed":false},{"type":"uint256","name":"offpeg_fee_multiplier","indexed":false}],"anonymous":false,"type":"event"},{"name":"RampA","inputs":[{"type":"uint256","name":"old_A","indexed":false},{"type":"uint256","name":"new_A","indexed":false},{"type":"uint256","name":"initial_time","indexed":false},{"type":"uint256","name":"future_time","indexed":false}],"anonymous":false,"type":"event"},{"name":"StopRampA","inputs":[{"type":"uint256","name":"A","indexed":false},{"type":"uint256","name":"t","indexed":false}],"anonymous":false,"type":"event"},{"outputs":[],"inputs":[{"type":"address[3]","name":"_coins"},{"type":"address[3]","name":"_underlying_coins"},{"type":"address","name":"_pool_token"},{"type":"address","name":"_aave_lending_pool"},{"type":"uint256","name":"_A"},{"type":"uint256","name":"_fee"},{"type":"uint256","name":"_admin_fee"},{"type":"uint256","name":"_offpeg_fee_multiplier"}],"stateMutability":"nonpayable","type":"constructor"},{"name":"A","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":5199},{"name":"A_precise","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":5161},{"name":"dynamic_fee","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"}],"stateMutability":"view","type":"function","gas":10278},{"name":"balances","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"i"}],"stateMutability":"view","type":"function","gas":2731},{"name":"get_virtual_price","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2680120},{"name":"calc_token_amount","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"bool","name":"is_deposit"}],"stateMutability":"view","type":"function","gas":5346581},{"name":"add_liquidity","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"uint256","name":"_min_mint_amount"}],"stateMutability":"nonpayable","type":"function"},{"name":"add_liquidity","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"uint256","name":"_min_mint_amount"},{"type":"bool","name":"_use_underlying"}],"stateMutability":"nonpayable","type":"function"},{"name":"get_dy","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"},{"type":"uint256","name":"dx"}],"stateMutability":"view","type":"function","gas":6239547},{"name":"get_dy_underlying","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"},{"type":"uint256","name":"dx"}],"stateMutability":"view","type":"function","gas":6239577},{"name":"exchange","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"},{"type":"uint256","name":"dx"},{"type":"uint256","name":"min_dy"}],"stateMutability":"nonpayable","type":"function","gas":6361682},{"name":"exchange_underlying","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"int128","name":"i"},{"type":"int128","name":"j"},{"type":"uint256","name":"dx"},{"type":"uint256","name":"min_dy"}],"stateMutability":"nonpayable","type":"function","gas":6369753},{"name":"remove_liquidity","outputs":[{"type":"uint256[3]","name":""}],"inputs":[{"type":"uint256","name":"_amount"},{"type":"uint256[3]","name":"_min_amounts"}],"stateMutability":"nonpayable","type":"function"},{"name":"remove_liquidity","outputs":[{"type":"uint256[3]","name":""}],"inputs":[{"type":"uint256","name":"_amount"},{"type":"uint256[3]","name":"_min_amounts"},{"type":"bool","name":"_use_underlying"}],"stateMutability":"nonpayable","type":"function"},{"name":"remove_liquidity_imbalance","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"uint256","name":"_max_burn_amount"}],"stateMutability":"nonpayable","type":"function"},{"name":"remove_liquidity_imbalance","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256[3]","name":"_amounts"},{"type":"uint256","name":"_max_burn_amount"},{"type":"bool","name":"_use_underlying"}],"stateMutability":"nonpayable","type":"function"},{"name":"calc_withdraw_one_coin","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"_token_amount"},{"type":"int128","name":"i"}],"stateMutability":"view","type":"function","gas":4449067},{"name":"remove_liquidity_one_coin","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"_token_amount"},{"type":"int128","name":"i"},{"type":"uint256","name":"_min_amount"}],"stateMutability":"nonpayable","type":"function"},{"name":"remove_liquidity_one_coin","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"_token_amount"},{"type":"int128","name":"i"},{"type":"uint256","name":"_min_amount"},{"type":"bool","name":"_use_underlying"}],"stateMutability":"nonpayable","type":"function"},{"name":"ramp_A","outputs":[],"inputs":[{"type":"uint256","name":"_future_A"},{"type":"uint256","name":"_future_time"}],"stateMutability":"nonpayable","type":"function","gas":151954},{"name":"stop_ramp_A","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":148715},{"name":"commit_new_fee","outputs":[],"inputs":[{"type":"uint256","name":"new_fee"},{"type":"uint256","name":"new_admin_fee"},{"type":"uint256","name":"new_offpeg_fee_multiplier"}],"stateMutability":"nonpayable","type":"function","gas":146482},{"name":"apply_new_fee","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":133744},{"name":"revert_new_parameters","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":21985},{"name":"commit_transfer_ownership","outputs":[],"inputs":[{"type":"address","name":"_owner"}],"stateMutability":"nonpayable","type":"function","gas":74723},{"name":"apply_transfer_ownership","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":60800},{"name":"revert_transfer_ownership","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":22075},{"name":"withdraw_admin_fees","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":71651},{"name":"donate_admin_fees","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":62276},{"name":"kill_me","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":38058},{"name":"unkill_me","outputs":[],"inputs":[],"stateMutability":"nonpayable","type":"function","gas":22195},{"name":"set_aave_referral","outputs":[],"inputs":[{"type":"uint256","name":"referral_code"}],"stateMutability":"nonpayable","type":"function","gas":37325},{"name":"coins","outputs":[{"type":"address","name":""}],"inputs":[{"type":"uint256","name":"arg0"}],"stateMutability":"view","type":"function","gas":2310},{"name":"underlying_coins","outputs":[{"type":"address","name":""}],"inputs":[{"type":"uint256","name":"arg0"}],"stateMutability":"view","type":"function","gas":2340},{"name":"admin_balances","outputs":[{"type":"uint256","name":""}],"inputs":[{"type":"uint256","name":"arg0"}],"stateMutability":"view","type":"function","gas":2370},{"name":"fee","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2291},{"name":"offpeg_fee_multiplier","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2321},{"name":"admin_fee","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2351},{"name":"owner","outputs":[{"type":"address","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2381},{"name":"lp_token","outputs":[{"type":"address","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2411},{"name":"initial_A","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2441},{"name":"future_A","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2471},{"name":"initial_A_time","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2501},{"name":"future_A_time","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2531},{"name":"admin_actions_deadline","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2561},{"name":"transfer_ownership_deadline","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2591},{"name":"future_fee","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2621},{"name":"future_admin_fee","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2651},{"name":"future_offpeg_fee_multiplier","outputs":[{"type":"uint256","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2681},{"name":"future_owner","outputs":[{"type":"address","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":2711}]
                """
                    ),
                )
                .functions.dynamic_fee(i, j)
                .call()
            )
            # compare calculated dynamic fee to contract fee
            assert dyn_fee == contract_fee, f"{dyn_fee=} != {contract_fee=}"
        else:
            _fee = self.fee * dy // self.FEE_DENOMINATOR

        return dy - _fee

    def _stored_rates(self):
        # ref: https://etherscan.io/address/0x59Ab5a5b5d617E478a2479B0cAD80DA7e2831492#code
        ORACLE_BIT_MASK = (2**32 - 1) * 256**28

        rates = self.rates
        oracle = self.oracle_method

        if oracle != 0:
            response = config.get_web3().eth.call(
                {
                    "to": HexBytes(oracle % 2**160),
                    "data": oracle & ORACLE_BIT_MASK,
                }
            )
            rates = (
                rates[0],
                rates[1] * int.from_bytes(response, byteorder="big") // self.PRECISION,
            )

        return rates

    def _get_y(
        self,
        i: int,
        j: int,
        x: int,
        xp: Iterable[int],
        _amp: Optional[int] = None,
        _D: Optional[int] = None,
    ) -> int:
        """
        Calculate x[j] if one makes x[i] = x

        Done by solving quadratic equation iteratively.
        x_1**2 + x_1 * (sum' - (A*n**n - 1) * D / (A * n**n)) = D ** (n + 1) / (n ** (2 * n) * prod' * A)
        x_1**2 + b*x_1 = c

        x_1 = (x_1**2 + c) / (2*x_1 + b)
        """

        # x in the input is converted to the same price/precision

        N_COINS = len(self.tokens)

        assert i != j, "same coin"
        assert j >= 0, "j below zero"
        assert j < N_COINS, "j above N_COINS"

        # should be unreachable, but good for safety
        assert i >= 0
        assert i < N_COINS

        amp = self._A() if _amp is None else _amp
        D = self._get_D(xp, amp) if _D is None else _D
        c = D
        Ann = amp * N_COINS

        S_ = 0
        _x = 0
        for _i in range(N_COINS):
            if _i == i:
                _x = x
            elif _i != j:
                _x = xp[_i]
            else:
                continue
            S_ += _x
            c = c * D // (_x * N_COINS)

        c = c * D // (Ann * N_COINS)
        b = S_ + D // Ann  # - D
        y = D
        for _ in range(255):
            y_prev = y
            y = (y * y + c) // (2 * y + b - D)
            # Equality with the precision of 1
            if y > y_prev:
                if y - y_prev <= 1:
                    break
            else:
                if y_prev - y <= 1:
                    break
        return y

    def _get_y_with_A_precision(
        self,
        i: int,
        j: int,
        x: int,
        xp: Iterable[int],
        _amp: Optional[int] = None,
        _D: Optional[int] = None,
    ) -> int:
        """
        Calculate x[j] if one makes x[i] = x

        Done by solving quadratic equation iteratively.
        x_1**2 + x_1 * (sum' - (A*n**n - 1) * D / (A * n**n)) = D ** (n + 1) / (n ** (2 * n) * prod' * A)
        x_1**2 + b*x_1 = c

        x_1 = (x_1**2 + c) / (2*x_1 + b)
        """

        N_COINS = len(self.tokens)
        # x in the input is converted to the same price/precision

        assert i != j, "same coin"
        assert j >= 0, "j below zero"
        assert j < N_COINS, "j above N_COINS"

        # should be unreachable, but good for safety
        assert i >= 0
        assert i < N_COINS

        amp = self._A_with_A_precision() if _amp is None else _amp
        D = self._get_D_with_A_precision(xp, amp) if _D is None else _D

        S_ = 0
        _x = 0
        y_prev = 0
        c = D
        Ann = amp * N_COINS

        for _i in range(N_COINS):
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
        self, rates: Optional[List[int]] = None, balances: Optional[List[int]] = None
    ) -> List[int]:
        if rates is None:
            rates = self.rates
        if balances is None:
            balances = self.balances

        return [rate * balance // self.PRECISION for rate, balance in zip(rates, balances)]

    def _A(
        self,
        timestamp: Optional[int] = None,
    ) -> int:
        """
        Handle ramping A up or down
        """

        if self.future_a_coefficient is None:
            # Some pools do not have a ramp, so return A directly
            return self.a_coefficient

        if timestamp is None:
            timestamp = self.future_a_coefficient_time + 1

        t1 = self.future_a_coefficient_time
        A1 = self.future_a_coefficient

        if (
            timestamp < t1
        ):  # <--- modified from contract template, takes timestamp instead of block.timestamp
            A0 = self.initial_a_coefficient
            t0 = self.initial_a_coefficient_time
            # Expressions in int cannot have negative numbers, thus "if"
            if A1 > A0:
                return A0 + (A1 - A0) * (timestamp - t0) // (t1 - t0)
            else:
                return A0 - (A0 - A1) * (timestamp - t0) // (t1 - t0)

        else:  # when t1 == 0 or timestamp >= t1
            return A1

    def _A_with_A_precision(
        self,
        timestamp: Optional[int] = None,
    ) -> int:
        """
        Handle ramping A up or down
        """

        if self.future_a_coefficient is None:
            # Some pools do not have a ramp, so return A directly
            return self.a_coefficient * self.A_PRECISION

        if timestamp is None:
            timestamp = self.future_a_coefficient_time + 1

        t1 = self.future_a_coefficient_time
        A1 = self.future_a_coefficient * self.A_PRECISION

        if (
            timestamp < t1
        ):  # <--- modified from contract template, takes timestamp instead of block.timestamp
            A0 = self.initial_a_coefficient
            t0 = self.initial_a_coefficient_time
            # Expressions in int cannot have negative numbers, thus "if"
            if A1 > A0:
                return A0 + (A1 - A0) * (timestamp - t0) // (t1 - t0)
            else:
                return A0 - (A0 - A1) * (timestamp - t0) // (t1 - t0)

        else:  # when t1 == 0 or timestamp >= t1
            return A1

    def _get_D(self, xp: List[int], amp: int) -> int:
        if any([x == 0 for x in xp]):
            zero_liquidity_tokens = [
                self.tokens[token_index]
                for token in self.tokens
                if self.balances[token_index := self.tokens.index(token)] == 0
            ]
            raise ZeroLiquidityError(f"Pool has no liquidity for tokens {zero_liquidity_tokens}")

        N_COINS = len(self.tokens)

        S = sum(xp)
        if S == 0:
            return 0

        Dprev = 0
        D = S
        Ann = amp * N_COINS

        for _ in range(255):
            D_P = D
            for _x in xp:
                D_P = D_P * D // (_x * N_COINS)
            Dprev = D
            D = (Ann * S + D_P * N_COINS) * D // ((Ann - 1) * D + (N_COINS + 1) * D_P)
            # Equality with the precision of 1
            if D > Dprev:
                if D - Dprev <= 1:
                    return D
            else:
                if Dprev - D <= 1:
                    return D

        raise EVMRevertError("get_D did not converge!")

    def _get_D_with_A_precision(self, _xp: List[int], _amp: int) -> int:
        N_COINS = len(self.tokens)

        if self.address in ("0xC61557C5d177bd7DC889A3b621eEC333e168f68A",):
            print(f"{_xp=}")
            print(f"{_amp=}")

            S = 0
            Dprev = 0
            for x in _xp:
                S += x
            if S == 0:
                return 0

            D = S
            Ann = _amp * N_COINS
            for _ in range(255):
                D_P = D
                for x in _xp:
                    D_P = (
                        D_P * D // (x * N_COINS)
                    )  # If division by 0, this will be borked: only withdrawal will work. And that is good
                Dprev = D
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
            raise
        else:
            S = sum(_xp)
            if S == 0:
                return 0

            D = S
            Ann = _amp * N_COINS
            for _ in range(255):
                D_P = D * D // _xp[0] * D // _xp[1] // (N_COINS**2)
                Dprev = D
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
            raise

    @property
    def _w3_contract(self) -> Contract:
        return config.get_web3().eth.contract(
            address=self.address,
            abi=self.abi,
        )

    @property
    def metaregistry(self) -> Contract:
        return config.get_web3().eth.contract(
            address=CURVE_METAREGISTRY_ADDRESS,
            abi=CURVE_METAREGISTRY_ABI,
        )

    def calculate_tokens_out_from_tokens_in(
        self,
        token_in: Erc20Token,
        token_out: Erc20Token,
        token_in_quantity: int,
        override_state: Optional[CurveStableswapPoolState] = None,
        dynamic_fee: bool = False,
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
            dynamic_fee=dynamic_fee,
        )
