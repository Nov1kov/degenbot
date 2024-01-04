import itertools

import degenbot
import eth_abi
import pytest
from degenbot.curve.abi import CURVE_V1_FACTORY_ABI, CURVE_V1_REGISTRY_ABI
from degenbot.curve.curve_stableswap_liquidity_pool import BrokenPool, CurveStableswapPool
from degenbot.curve.curve_stableswap_dataclasses import CurveStableswapPoolExternalUpdate
from degenbot.exceptions import ZeroLiquidityError, ZeroSwapError
from degenbot.fork import AnvilFork
from web3 import Web3
from web3.contract import Contract

FRXETH_WETH_CURVE_POOL_ADDRESS = "0x9c3B46C0Ceb5B9e304FCd6D88Fc50f7DD24B31Bc"
CURVE_V1_FACTORY_ADDRESS = "0x127db66E7F0b16470Bec194d0f496F9Fa065d0A9"
CURVE_V1_REGISTRY_ADDRESS = "0x90E00ACe148ca3b23Ac1bC8C240C2a7Dd9c2d7f5"
TRIPOOL_ADDRESS = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"


@pytest.fixture()
def fork_from_archive() -> AnvilFork:
    return AnvilFork(fork_url="http://localhost:8543")


@pytest.fixture()
def tripool(fork_from_archive: AnvilFork) -> CurveStableswapPool:
    degenbot.set_web3(fork_from_archive.w3)
    return CurveStableswapPool(TRIPOOL_ADDRESS)


def _test_calculations(lp: CurveStableswapPool):
    state_block = lp.update_block

    for token_in_index, token_out_index in itertools.permutations(range(len(lp.tokens)), 2):
        token_in = lp.tokens[token_in_index]
        token_out = lp.tokens[token_out_index]

        for amount_multiplier in [0.01, 0.05, 0.25]:
            amount = int(amount_multiplier * lp.balances[lp.tokens.index(token_in)])

            try:
                calc_amount = lp.calculate_tokens_out_from_tokens_in(
                    token_in=token_in,
                    token_out=token_out,
                    token_in_quantity=amount,
                )
            except (ZeroSwapError, ZeroLiquidityError) as e:
                print(f"Zero swap: {e}")
                continue

            if lp.address == "0x80466c64868E1ab14a1Ddf27A676C3fcBE638Fe5":
                tx = {
                    "to": lp.address,
                    "data": Web3.keccak(text="get_dy(uint256,uint256,uint256)")[:4]
                    + eth_abi.encode(
                        types=["uint256", "uint256", "uint256"],
                        args=[token_in_index, token_out_index, amount],
                    ),
                }
                contract_amount, *_ = eth_abi.decode(
                    data=degenbot.get_web3().eth.call(
                        transaction=tx,
                        block_identifier=state_block,
                    ),
                    types=["uint256"],
                )
            else:
                contract_amount = lp._w3_contract.functions.get_dy(
                    token_in_index,
                    token_out_index,
                    amount,
                ).call(block_identifier=state_block)

            assert (
                calc_amount == contract_amount
            ), f"Failure simulating swap @ {lp.address}: {amount} {token_in} for {token_out}"

    if lp.is_metapool:
        for token_in, token_out in itertools.permutations(lp.tokens_underlying, 2):
            token_in_index = lp.tokens_underlying.index(token_in)
            token_out_index = lp.tokens_underlying.index(token_out)

            for amount_multiplier in [0.01, 0.05, 0.25]:
                if token_in in lp.tokens:
                    amount = int(amount_multiplier * lp.balances[lp.tokens.index(token_in)])
                else:
                    amount = int(
                        amount_multiplier
                        * lp.base_pool.balances[lp.base_pool.tokens.index(token_in)]
                    )

                try:
                    calc_amount = lp.calculate_tokens_out_from_tokens_in(
                        token_in=token_in,
                        token_out=token_out,
                        token_in_quantity=amount,
                    )
                except (ZeroSwapError, ZeroLiquidityError) as e:
                    print(f"Skipping zero swap: {e}")
                    continue

                contract_amount = lp._w3_contract.functions.get_dy_underlying(
                    token_in_index,
                    token_out_index,
                    amount,
                ).call(block_identifier=state_block)

                assert (
                    calc_amount == contract_amount
                ), f"Failure simulating swap @ {lp.address}: {amount} {token_in} for {token_out}"


def test_create_pool(fork_from_archive: AnvilFork):
    degenbot.set_web3(fork_from_archive.w3)
    CurveStableswapPool(address=FRXETH_WETH_CURVE_POOL_ADDRESS, silent=True)


def test_metapool(fork_from_archive: AnvilFork):
    degenbot.set_web3(fork_from_archive.w3)
    gusd_metapool = CurveStableswapPool("0x4f062658EaAF2C1ccf8C8e36D6824CDf41167956")
    _test_calculations(gusd_metapool)


def test_tripool(tripool: CurveStableswapPool):
    _test_calculations(tripool)


def test_auto_update(fork_from_archive: AnvilFork):
    # Build the pool at a known historical block
    _BLOCK_NUMBER = 18849427 - 1
    fork_from_archive.reset(block_number=_BLOCK_NUMBER)
    degenbot.set_web3(fork_from_archive.w3)

    _tripool = CurveStableswapPool(TRIPOOL_ADDRESS)

    assert fork_from_archive.w3.eth.block_number == _BLOCK_NUMBER
    assert _tripool.update_block == _BLOCK_NUMBER

    _EXPECTED_BALANCES = [75010632422398781503259123, 76382820384826, 34653521595900]
    assert _tripool.balances == _EXPECTED_BALANCES

    fork_from_archive.reset(block_number=_BLOCK_NUMBER + 1)
    assert fork_from_archive.w3.eth.block_number == _BLOCK_NUMBER + 1
    _tripool.auto_update()
    assert _tripool.update_block == _BLOCK_NUMBER + 1
    assert _tripool.balances == [
        75010632422398781503259123,
        76437030384826,
        34599346168546,
    ]


def test_external_update(fork_from_archive: AnvilFork):
    # Build the pool at a known historical block
    _BLOCK_NUMBER = 18849427 - 1
    fork_from_archive.reset(block_number=_BLOCK_NUMBER)
    degenbot.set_web3(fork_from_archive.w3)

    _tripool = CurveStableswapPool(TRIPOOL_ADDRESS)

    assert fork_from_archive.w3.eth.block_number == _BLOCK_NUMBER
    assert _tripool.update_block == _BLOCK_NUMBER

    _EXPECTED_BALANCES = [75010632422398781503259123, 76382820384826, 34653521595900]
    assert _tripool.balances == _EXPECTED_BALANCES

    _SOLD_ID = 1
    _TOKENS_SOLD = 54210000000
    _BOUGHT_ID = 2
    _TOKENS_BOUGHT = 54172718448

    # ref: https://etherscan.io/tx/0x34cd3858eab8ac17a2ef0fd483da48e077d910075d392ab3d510ca6d5e6b4cce
    update = CurveStableswapPoolExternalUpdate(
        block_number=fork_from_archive.w3.eth.block_number + 1,
        sold_id=_SOLD_ID,
        bought_id=_BOUGHT_ID,
        tokens_sold=_TOKENS_SOLD,
        tokens_bought=_TOKENS_BOUGHT,
    )
    _tripool.external_update(update)

    assert _tripool.balances == [
        75010632422398781503259123,
        76437030384826,
        34599346168546,
    ]


def test_A_ramping():
    # A range:      5000 -> 2000
    # A time :      1653559305 -> 1654158027

    INITIAL_A = 5000
    FINAL_A = 2000

    INITIAL_A_TIME = 1653559305
    FINAL_A_TIME = 1654158027

    fork = AnvilFork(
        fork_url="http://localhost:8543",
        fork_block=14_900_000,
    )
    degenbot.set_web3(fork.w3)

    tripool = CurveStableswapPool(address="0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7", silent=True)
    print(fork.w3.eth.get_block("latest")["timestamp"])

    assert tripool._A(timestamp=INITIAL_A_TIME) == INITIAL_A
    assert tripool._A(timestamp=FINAL_A_TIME) == FINAL_A
    assert tripool._A(timestamp=(INITIAL_A_TIME + FINAL_A_TIME) // 2) == (INITIAL_A + FINAL_A) // 2


def test_base_registry_pools(fork_from_archive: AnvilFork):
    """
    Test the custom pools deployed by Curve
    """
    degenbot.set_web3(fork_from_archive.w3)

    registry: Contract = fork_from_archive.w3.eth.contract(
        address=CURVE_V1_REGISTRY_ADDRESS,
        abi=CURVE_V1_REGISTRY_ABI,
    )
    pool_count = registry.functions.pool_count().call()

    for pool_id in range(pool_count):
        pool_address = registry.functions.pool_list(pool_id).call()
        print(f"{pool_id}: {pool_address=}")
        lp = CurveStableswapPool(address=pool_address, silent=True)
        _test_calculations(lp)


def test_single_pool(fork_from_archive: AnvilFork):
    degenbot.set_web3(fork_from_archive.w3)

    POOL_ADDRESS = "0x3F1B0278A9ee595635B61817630cC19DE792f506"

    lp = CurveStableswapPool(address=POOL_ADDRESS)
    _test_calculations(lp)


def test_base_pool(fork_from_archive: AnvilFork):
    degenbot.set_web3(fork_from_archive.w3)

    POOL_ADDRESS = "0xf253f83AcA21aAbD2A20553AE0BF7F65C755A07F"

    basepool = CurveStableswapPool(address=POOL_ADDRESS, silent=True)

    # Compare withdrawal calc for all tokens in the pool
    for token_index, token in enumerate(basepool.tokens):
        print(f"Testing {token} withdrawal")
        for amount_multiplier in [0.01, 0.10, 0.25]:
            token_in_amount = int(amount_multiplier * basepool.balances[token_index])
            print(f"Withdrawing {token_in_amount} {token}")
            calc_amount, *_ = basepool._calc_withdraw_one_coin(
                _token_amount=token_in_amount, i=token_index
            )

            amount_contract, *_ = eth_abi.decode(
                types=["uint256"],
                data=fork_from_archive.w3.eth.call(
                    transaction={
                        "to": basepool.address,
                        "data": Web3.keccak(text="calc_withdraw_one_coin(uint256,int128)")[:4]
                        + eth_abi.encode(
                            types=["uint256", "int128"],
                            args=[token_in_amount, token_index],
                        ),
                    }
                ),
            )
            assert calc_amount == amount_contract

    for token_index, token in enumerate(basepool.tokens):
        print(f"Testing {token} calc token amount")

        amount_array = [0] * len(basepool.tokens)

        for amount_multiplier in [0.01, 0.10, 0.25]:
            token_in_amount = int(amount_multiplier * basepool.balances[token_index])
            amount_array[token_index] = token_in_amount
            print(f"{token_in_amount=}")
            calc_token_amount = basepool._calc_token_amount(
                amounts=amount_array,
                deposit=True,
            )

            calc_token_amount_contract, *_ = eth_abi.decode(
                types=["uint256"],
                data=fork_from_archive.w3.eth.call(
                    transaction={
                        "to": basepool.address,
                        "data": Web3.keccak(
                            text=f"calc_token_amount(uint256[{len(basepool.tokens)}],bool)"
                        )[:4]
                        + eth_abi.encode(
                            types=[f"uint256[{len(basepool.tokens)}]", "bool"],
                            args=[amount_array, True],
                        ),
                    }
                ),
            )
            assert calc_token_amount == calc_token_amount_contract


def test_factory_stableswap_pools(fork_from_archive: AnvilFork):
    """
    Test the user-deployed pools deployed by the factory
    """
    degenbot.set_web3(fork_from_archive.w3)

    stableswap_factory: Contract = fork_from_archive.w3.eth.contract(
        address=CURVE_V1_FACTORY_ADDRESS, abi=CURVE_V1_FACTORY_ABI
    )
    pool_count = stableswap_factory.functions.pool_count().call()

    for pool_id in range(pool_count):
        pool_address = stableswap_factory.functions.pool_list(pool_id).call()

        try:
            lp = CurveStableswapPool(address=pool_address, silent=True)
            _test_calculations(lp)
        except (BrokenPool, ZeroLiquidityError):
            continue
        except Exception as e:
            print(f"{type(e)}: {e} - {pool_address=}")
            raise
