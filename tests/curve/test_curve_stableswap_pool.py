import itertools

import degenbot
import eth_abi
import pytest
from degenbot.curve.abi import CURVE_V1_FACTORY_ABI, CURVE_V1_REGISTRY_ABI
from degenbot.curve.curve_stableswap_liquidity_pool import (
    BROKEN_POOLS,
    BrokenPool,
    CurveStableswapPool,
)
from degenbot.exceptions import ZeroLiquidityError, ZeroSwapError
from degenbot.fork import AnvilFork
from web3 import Web3
from web3.contract import Contract

FRXETH_WETH_CURVE_POOL_ADDRESS = "0x9c3B46C0Ceb5B9e304FCd6D88Fc50f7DD24B31Bc"
CURVE_V1_FACTORY_ADDRESS = "0x127db66E7F0b16470Bec194d0f496F9Fa065d0A9"
CURVE_V1_REGISTRY_ADDRESS = "0x90E00ACe148ca3b23Ac1bC8C240C2a7Dd9c2d7f5"


@pytest.fixture()
def fork_from_archive() -> Web3:
    return AnvilFork(fork_url="http://localhost:8543")


def _test_calculations(lp: CurveStableswapPool):
    state_block = lp.update_block

    for token_in_index, token_out_index in itertools.permutations(range(len(lp.tokens)), 2):
        token_in = lp.tokens[token_in_index]
        token_out = lp.tokens[token_out_index]

        for amount_multiplier in [0.01, 0.05, 0.25]:
            amount = int(amount_multiplier * lp.balances[lp.tokens.index(token_in)])

            print(f"Simulating swap: {amount} {token_in} for {token_out}")

            try:
                calc_amount = lp.calculate_tokens_out_from_tokens_in(
                    token_in=token_in,
                    token_out=token_out,
                    token_in_quantity=amount,
                )
            except ZeroSwapError as e:
                print(f"Skipping zero swap: {e}")
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

            assert calc_amount == contract_amount


def test_create_pool(fork_from_archive: AnvilFork):
    degenbot.set_web3(fork_from_archive.w3)
    lp = CurveStableswapPool(address=FRXETH_WETH_CURVE_POOL_ADDRESS, silent=True)

    # Test providing tokens
    CurveStableswapPool(address=FRXETH_WETH_CURVE_POOL_ADDRESS, tokens=lp.tokens)

    # Test with the wrong tokens
    with pytest.raises(ValueError, match=f"Token {lp.tokens[1].address} not found in tokens."):
        CurveStableswapPool(
            address=FRXETH_WETH_CURVE_POOL_ADDRESS, tokens=[lp.tokens[0]], silent=True
        )


def test_tripool(fork_from_archive: AnvilFork):
    degenbot.set_web3(fork_from_archive.w3)
    tripool = CurveStableswapPool("0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7")

    _test_calculations(tripool)


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
        # print(f"{pool_id}: {pool_address=}")
        lp = CurveStableswapPool(address=pool_address)
        _test_calculations(lp)


def test_single_pool(fork_from_archive: AnvilFork):
    degenbot.set_web3(fork_from_archive.w3)

    POOL_ADDRESS = "0xDeBF20617708857ebe4F679508E7b7863a8A8EeE"

    lp = CurveStableswapPool(address=POOL_ADDRESS, silent=True)
    _test_calculations(lp)


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

        if pool_address in BROKEN_POOLS:
            continue

        try:
            lp = CurveStableswapPool(address=pool_address, silent=True)
            _test_calculations(lp)
        except (BrokenPool, ZeroLiquidityError):
            continue
        except Exception as e:
            print(f"{type(e)}: {e} - {pool_address=}")
            raise
