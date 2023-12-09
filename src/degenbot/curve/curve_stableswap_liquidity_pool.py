from threading import Lock
from typing import Dict, Iterable, List, Optional, Set, Union

from eth_typing import ChecksumAddress
from eth_utils.address import to_checksum_address
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
import eth_abi
from .. import config
from ..baseclasses import PoolHelper
from ..constants import ZERO_ADDRESS
from ..erc20_token import Erc20Token
from ..exceptions import (
    EVMRevertError,
    LiquidityPoolError,
    ZeroLiquidityError,
    ZeroSwapError,
)
from ..logging import logger
from ..manager import Erc20TokenHelperManager
from ..registry import AllPools
from ..subscription_mixins import Subscriber, SubscriptionMixin
from .abi import CURVE_METAREGISTRY_ABI, CURVE_V1_POOL_ABI
from .curve_stableswap_dataclasses import CurveStableswapPoolState

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
    PRECISION_DECIMALS = 18
    PRECISION = 10**PRECISION_DECIMALS
    LENDING_PRECISION = PRECISION
    FEE_DENOMINATOR = 10**10
    A_PRECISION = 100

    def __init__(
        self,
        address: Union[ChecksumAddress, str],
        tokens: Optional[List[Erc20Token]] = None,
        a_coefficient: Optional[int] = None,
        name: Optional[str] = None,
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

        if self.address == "0xEB16Ae0052ed37f479f7fe63849198Df1765a733":
            self.offpeg_fee_multiplier, *_ = eth_abi.decode(
                data=_w3.eth.call(
                    {
                        "to": self.address,
                        "data": HexBytes(Web3.keccak(text="offpeg_fee_multiplier()"))[:4],
                    }
                ),
                types=["uint256"],
            )

        if self.address == "0xDeBF20617708857ebe4F679508E7b7863a8A8EeE":
            self.precision_multipliers = [1, 1000000000000, 1000000000000]
            self.offpeg_fee_multiplier, *_ = eth_abi.decode(
                data=_w3.eth.call(
                    {
                        "to": self.address,
                        "data": HexBytes(Web3.keccak(text="offpeg_fee_multiplier()"))[:4],
                    }
                ),
                types=["uint256"],
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
        # rate_multipliers = [
        #   1000000000000000000,             <------ 10**18 == 10**(18 + 18 - 18)
        #   1000000000000000000000000000000, <------ 10**30 == 10**(18 + 18 - 6)
        #   1000000000000000000000000000000, <------ 10**30 == 10**(18 + 18 - 6)
        # ]

        self.rate_multipliers = tuple(
            [10 ** (2 * self.PRECISION_DECIMALS - token.decimals) for token in self.tokens]
        )
        self.precision_multipliers = tuple(
            [10 ** (self.PRECISION_DECIMALS - token.decimals) for token in self.tokens]
        )

        if self.address == "0xA2B47E3D5c44877cca798226B7B8118F9BFb7A56":
            self.USE_LENDING = [True, True]
            # TODO: investigate why this isn't 10**10, 10**10
            # since both tokens have 8 decimal places
            self.precision_multipliers = [1, 10**12]

        if self.address == "0x06364f10B501e868329afBc005b3492902d6C763":
            self.USE_LENDING = [True, True, True, False]

        if self.address == "0xDeBF20617708857ebe4F679508E7b7863a8A8EeE":
            self.precision_multipliers = [1, 1000000000000, 1000000000000]

        if self.address == "0x2dded6Da1BF5DBdF597C45fcFaa3194e53EcfeAF":
            self.precision_multipliers = [1, 1000000000000, 1000000000000]

        if self.address == "0x79a8C46DeA5aDa233ABaFFD40F3A0A2B1e5A4F27":
            self.precision_multipliers = [1, 1000000000000, 1000000000000, 1]
            self.USE_LENDING = [True, True, True, True]

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

    def _get_dy(self, i: int, j: int, dx: int) -> int:
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
            live_balances = [token.get_balance(self.address) for token in self.tokens]
            admin_balances = self.metaregistry.functions.get_admin_balances(self.address).call()[
                : len(self.tokens)
            ]
            balances = [
                pool_balance - admin_balance
                for pool_balance, admin_balance in zip(live_balances, admin_balances)
            ]
            rates = self.rate_multipliers
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
            "0x857110B5f8eFD66CC3762abb935315630AC770B5",
            "0x21B45B2c1C53fDFe378Ed1955E8Cc29aE8cE0132",
            "0x602a9Abb10582768Fd8a9f13aD6316Ac2A5A2e2B",
            "0x0Ce6a5fF5217e38315f87032CF90686C96627CAA",
        ):
            rates = self.rate_multipliers
            xp = self._xp_mem(rates, self.balances)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y(i, j, x, xp)
            dy = xp[j] - y - 1
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return (dy - fee) * self.PRECISION // rates[j]

        elif self.address in (
            "0x48fF31bBbD8Ab553Ebe7cBD84e1eA3dBa8f54957",
            "0x320B564Fb9CF36933eC507a846ce230008631fd3",
            "0x875DF0bA24ccD867f8217593ee27253280772A97",
        ):
            xp = self.balances
            x = xp[i] + dx
            y = self._get_y_with_A_precision(i, j, x, xp)
            dy = xp[j] - y - 1
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return dy - fee

        elif self.address in ("0x59Ab5a5b5d617E478a2479B0cAD80DA7e2831492",):
            live_balances = [token.get_balance(self.address) for token in self.tokens]
            admin_balances = self.metaregistry.functions.get_admin_balances(self.address).call()[
                : len(self.tokens)
            ]
            balances = [
                pool_balance - admin_balance
                for pool_balance, admin_balance in zip(live_balances, admin_balances)
            ]
            rates = self._stored_rates_from_oracle()
            xp = self._xp_mem(rates, self.balances)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y_with_A_precision(i, j, x, xp)
            dy = xp[j] - y - 1
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return (dy - fee) * self.PRECISION // rates[j]

        elif self.address in ("0xA2B47E3D5c44877cca798226B7B8118F9BFb7A56",):
            rates = self._stored_rates_from_ctokens()
            xp = self._xp_mem(rates)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y(i, j, x, xp)
            dy = (xp[j] - y) * self.PRECISION // rates[j]
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return dy - fee

        elif self.address in ("0x2dded6Da1BF5DBdF597C45fcFaa3194e53EcfeAF",):
            assert self.precision_multipliers == [1, 1000000000000, 1000000000000]
            rates = self._stored_rates_from_cytokens()
            xp = self._xp_mem(rates)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y_with_A_precision(i, j, x, xp)
            dy = xp[j] - y - 1
            return (dy - (self.fee * dy // self.FEE_DENOMINATOR)) * self.PRECISION // rates[j]

        elif self.address in ("0x06364f10B501e868329afBc005b3492902d6C763",):
            rates = self._stored_rates_from_ytokens()
            print(f"{rates=}")
            xp = self._xp_mem(rates, self.balances)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y(i, j, x, xp)
            dy = (xp[j] - y - 1) * self.PRECISION // rates[j]
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return dy - fee

        elif self.address in ("0x79a8C46DeA5aDa233ABaFFD40F3A0A2B1e5A4F27",):
            rates = self._stored_rates_from_ytokens()
            xp = self._xp_mem(rates, self.balances)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y(i, j, x, xp)
            dy = (xp[j] - y) * self.PRECISION // rates[j]
            fee = self.fee * dy // self.FEE_DENOMINATOR
            return dy - fee

        elif self.address in ("0xA96A65c051bF88B4095Ee1f2451C2A9d43F53Ae2",):
            rates = self._stored_rates_from_token1_ratio()
            xp = self._xp_mem(rates, self.balances)
            x = xp[i] + (dx * rates[i] // self.PRECISION)
            y = self._get_y_with_A_precision(i, j, x, xp)
            dy = xp[j] - y
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
                    self.rate_multipliers[0],
                    self.base_pool._w3_contract.functions.get_virtual_price().call(),
                ]
                xp = self._xp_mem(rates=_rates)
                x = xp[i] + (dx * _rates[i] // self.PRECISION)
                y = self._get_y(i, j, x, xp)

            dy = xp[j] - y - 1
            _fee = self.fee * dy // self.FEE_DENOMINATOR
            return (dy - _fee) * self.PRECISION // _rates[j]

        elif self.address in ("0xEB16Ae0052ed37f479f7fe63849198Df1765a733",):
            live_balances = [token.get_balance(self.address) for token in self.tokens]
            admin_balances = self.metaregistry.functions.get_admin_balances(self.address).call()[
                : len(self.tokens)
            ]
            balances = [
                pool_balance - admin_balance
                for pool_balance, admin_balance in zip(live_balances, admin_balances)
            ]
            xp = balances

            x = xp[i] + dx
            y = self._get_y(i, j, x, xp)
            dy = xp[j] - y
            _fee = (
                _dynamic_fee(
                    xpi=(xp[i] + x) // 2,
                    xpj=(xp[j] + y) // 2,
                    _fee=self.fee,
                    _feemul=self.offpeg_fee_multiplier,
                )
                * dy
                // self.FEE_DENOMINATOR
            )
            return dy - _fee

        elif self.address in (
            # dynamic fee pools
            "0xDeBF20617708857ebe4F679508E7b7863a8A8EeE",
        ):
            live_balances = [token.get_balance(self.address) for token in self.tokens]
            admin_balances = self.metaregistry.functions.get_admin_balances(self.address).call()[
                : len(self.tokens)
            ]
            balances = [
                pool_balance - admin_balance
                for pool_balance, admin_balance in zip(live_balances, admin_balances)
            ]

            precisions = self.precision_multipliers
            assert precisions == [1, 1000000000000, 1000000000000]
            xp = [balance * rate for balance, rate in zip(balances, precisions)]

            x = xp[i] + dx * precisions[i]
            y = self._get_y_with_A_precision(i, j, x, xp)
            dy = (xp[j] - y) // precisions[j]

            _fee = (
                _dynamic_fee(
                    xpi=(xp[i] + x) // 2,
                    xpj=(xp[j] + y) // 2,
                    _fee=self.fee,
                    _feemul=self.offpeg_fee_multiplier,
                )
                * dy
                // self.FEE_DENOMINATOR
            )
            return dy - _fee

        # dx and dy in c-units
        rates = self.rate_multipliers
        xp = self._xp_mem()

        x = xp[i] + (dx * rates[i] // self.PRECISION)
        y = self._get_y(i, j, x, xp)
        dy = (xp[j] - y - 1) * self.PRECISION // rates[j]

        _fee = self.fee * dy // self.FEE_DENOMINATOR

        return dy - _fee

    def _stored_rates_from_ctokens(self):
        # exchangeRateStored * (1 + supplyRatePerBlock * (getBlockNumber - accrualBlockNumber) / 1e18)
        result = []
        print(f"{self.precision_multipliers=}")
        for token, use_lending, multiplier in zip(
            self.tokens,
            self.USE_LENDING,
            self.precision_multipliers,
        ):
            if use_lending:
                rate = int.from_bytes(
                    config.get_web3().eth.call(
                        {
                            "to": HexBytes(token.address),
                            "data": Web3.keccak(text="exchangeRateStored()"),
                        }
                    )
                )
                supply_rate = int.from_bytes(
                    config.get_web3().eth.call(
                        {
                            "to": HexBytes(token.address),
                            "data": Web3.keccak(text="supplyRatePerBlock()"),
                        }
                    )
                )
                old_block = int.from_bytes(
                    config.get_web3().eth.call(
                        {
                            "to": HexBytes(token.address),
                            "data": Web3.keccak(text="accrualBlockNumber()"),
                        }
                    )
                )
                # TODO: check if +1 is needed to simulate next block
                next_block = config.get_web3().eth.get_block_number()
                rate += rate * supply_rate * (next_block - old_block) // self.LENDING_PRECISION
            result.append(multiplier * rate)
        return result

    def _stored_rates_from_ytokens(self):
        # ref: https://etherscan.io/address/0x79a8C46DeA5aDa233ABaFFD40F3A0A2B1e5A4F27#code

        result = []

        for token, multiplier, is_lending in zip(
            self.tokens, self.precision_multipliers, self.USE_LENDING
        ):
            if is_lending:
                rate, *_ = eth_abi.decode(
                    data=config.get_web3().eth.call(
                        {
                            "to": HexBytes(token.address),
                            "data": Web3.keccak(text="getPricePerFullShare()"),
                        }
                    ),
                    types=["uint256"],
                )
            else:
                rate = self.LENDING_PRECISION
            result.append(rate * multiplier)

        # return [
        #     multiplier * price_per_share
        #     for multiplier, price_per_share in zip(precision_multipliers, prices_per_share)
        # ]

        return result

    def _stored_rates_from_cytokens(self):
        # exchangeRateStored * (1 + supplyRatePerBlock * (getBlockNumber - accrualBlockNumber) / 1e18)

        result = []

        next_block = config.get_web3().eth.get_block_number()

        for coin, precision_multiplier in zip(self.tokens, self.precision_multipliers):
            rate, *_ = eth_abi.decode(
                data=(
                    config.get_web3().eth.call(
                        {
                            "to": HexBytes(coin.address),
                            "data": Web3.keccak(text="exchangeRateStored()"),
                        }
                    )
                ),
                types=["uint256"],
            )
            supply_rate, *_ = eth_abi.decode(
                data=config.get_web3().eth.call(
                    {
                        "to": HexBytes(coin.address),
                        "data": Web3.keccak(text="supplyRatePerBlock()"),
                    }
                ),
                types=["uint256"],
            )
            old_block, *_ = eth_abi.decode(
                data=config.get_web3().eth.call(
                    {
                        "to": HexBytes(coin.address),
                        "data": Web3.keccak(text="accrualBlockNumber()"),
                    }
                ),
                types=["uint256"],
            )

            rate += rate * supply_rate * (next_block - old_block) // self.PRECISION

            result.append(precision_multiplier * rate)

        print(f"{result=}")
        return result

    def _stored_rates_from_token1_ratio(self):
        # ref: https://etherscan.io/address/0xA96A65c051bF88B4095Ee1f2451C2A9d43F53Ae2#code
        aeth_ratio = int.from_bytes(
            config.get_web3().eth.call(
                {"to": HexBytes(self.tokens[1].address), "data": Web3.keccak(text="ratio()")}
            )
        )
        return [
            self.PRECISION,
            self.PRECISION * self.LENDING_PRECISION // aeth_ratio,
        ]

    def _stored_rates_from_oracle(self):
        # ref: https://etherscan.io/address/0x59Ab5a5b5d617E478a2479B0cAD80DA7e2831492#code
        ORACLE_BIT_MASK = (2**32 - 1) * 256**28

        rates = self.rate_multipliers
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

        if self.address in (
            "0x79a8C46DeA5aDa233ABaFFD40F3A0A2B1e5A4F27",
            "0xA2B47E3D5c44877cca798226B7B8118F9BFb7A56",
        ):
            if _amp is None:
                _amp = self.a_coefficient

            assert (i != j) and (i >= 0) and (j >= 0) and (i < N_COINS) and (j < N_COINS)

            D = self._get_D(xp)
            c = D
            S_ = 0
            Ann = _amp * N_COINS

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
            y_prev = 0
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

        else:
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

        for _ in range(255):
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
            rates = self.rate_multipliers
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

    def _get_D(self, xp: List[int], amp: Optional[int] = None) -> int:
        if any([x == 0 for x in xp]):
            zero_liquidity_tokens = [
                self.tokens[token_index]
                for token in self.tokens
                if self.balances[token_index := self.tokens.index(token)] == 0
            ]
            raise ZeroLiquidityError(f"Pool has no liquidity for tokens {zero_liquidity_tokens}")

        N_COINS = len(self.tokens)

        if self.address in (
            "0x79a8C46DeA5aDa233ABaFFD40F3A0A2B1e5A4F27",
            "0xA2B47E3D5c44877cca798226B7B8118F9BFb7A56",
        ):
            if amp is None:
                amp = self.a_coefficient

            S = sum(xp)
            if S == 0:
                return 0

            Dprev = 0
            D = S
            Ann = amp * N_COINS
            for _ in range(255):
                D_P = D
                for _x in xp:
                    D_P = D_P * D // (_x * N_COINS + 1)  # +1 is to prevent /0
                Dprev = D
                D = (Ann * S + D_P * N_COINS) * D // ((Ann - 1) * D + (N_COINS + 1) * D_P)
                # Equality with the precision of 1
                if D > Dprev:
                    if D - Dprev <= 1:
                        break
                else:
                    if Dprev - D <= 1:
                        break
            return D
        else:
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
            raise EVMRevertError

        elif self.address in ("0xDeBF20617708857ebe4F679508E7b7863a8A8EeE",):
            S = 0

            for _x in _xp:
                S += _x
            if S == 0:
                return 0

            Dprev = 0
            D = S
            Ann = _amp * N_COINS
            for _i in range(255):
                D_P = D
                for _x in _xp:
                    D_P = D_P * D // (_x * N_COINS + 1)  # +1 is to prevent /0
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
            raise EVMRevertError

        elif self.address in ("0x2dded6Da1BF5DBdF597C45fcFaa3194e53EcfeAF",):
            S = 0
            Dprev = 0

            S = sum(_xp)
            if S == 0:
                return 0

            D = S
            Ann = _amp * N_COINS
            for _ in range(255):
                D_P = D
                for _x in _xp:
                    D_P = (
                        D_P * D // (_x * N_COINS)
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
            raise EVMRevertError

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
            raise EVMRevertError

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
