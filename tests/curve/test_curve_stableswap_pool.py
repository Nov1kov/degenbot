import pytest
from degenbot.curve.curve_stableswap_liquidity_pool import CurveStableswapPool
import degenbot
from web3 import Web3
from degenbot.exceptions import ZeroSwapError

FRXETH_WETH_CURVE_POOL_ADDRESS = "0x9c3B46C0Ceb5B9e304FCd6D88Fc50f7DD24B31Bc"


@pytest.fixture(scope="function")
def frxeth_weth_curve_stableswap_pool(local_web3_ethereum_archive: Web3) -> CurveStableswapPool:
    degenbot.set_web3(local_web3_ethereum_archive)
    return CurveStableswapPool(FRXETH_WETH_CURVE_POOL_ADDRESS)


def test_create_pool(local_web3_ethereum_full: Web3):
    degenbot.set_web3(local_web3_ethereum_full)
    lp = CurveStableswapPool(FRXETH_WETH_CURVE_POOL_ADDRESS)

    # Test providing tokens
    CurveStableswapPool(address=FRXETH_WETH_CURVE_POOL_ADDRESS, tokens=lp.tokens)

    # Test with the wrong tokens
    with pytest.raises(ValueError, match=f"Token {lp.tokens[1].address} not found in tokens."):
        CurveStableswapPool(
            address=FRXETH_WETH_CURVE_POOL_ADDRESS,
            tokens=[lp.tokens[0]],
        )


def test_calculations(frxeth_weth_curve_stableswap_pool: CurveStableswapPool):
    lp = frxeth_weth_curve_stableswap_pool

    contract_balances = lp._w3_contract.functions.get_balances().call()
    assert contract_balances == lp.balances

    token_in_index = 0
    token_out_index = 1
    token_in = lp.tokens[token_in_index]
    token_out = lp.tokens[token_out_index]

    for amount in [1 * 10**18, 10 * 10**18, 100 * 10**18]:
        calc_amount = lp.calculate_tokens_out_from_tokens_in(
            token_in=token_in,
            token_out=token_out,
            token_in_quantity=amount,
        )

        contract_amount = lp._w3_contract.functions.get_dy(
            token_in_index,
            token_out_index,
            amount,
        ).call()

        assert calc_amount == contract_amount

    for amount in [1 * 10**18, 10 * 10**18, 100 * 10**18]:
        calc_amount = lp.calculate_tokens_in_from_tokens_out(
            token_in=token_in,
            token_out=token_out,
            token_out_quantity=amount,
        )

        contract_amount = lp._w3_contract.functions.get_dx(
            token_in_index,
            token_out_index,
            amount,
        ).call()

        assert calc_amount == contract_amount

    with pytest.raises(ZeroSwapError):
        calc_amount = lp.calculate_tokens_out_from_tokens_in(
            token_in=token_in,
            token_out=token_out,
            token_in_quantity=0,
        )

    with pytest.raises(ZeroSwapError):
        calc_amount = lp.calculate_tokens_in_from_tokens_out(
            token_in=token_in,
            token_out=token_out,
            token_out_quantity=0,
        )
