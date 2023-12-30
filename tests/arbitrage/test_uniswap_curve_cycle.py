import pytest
from degenbot.arbitrage.uniswap_curve_cycle import UniswapCurveCycle
from degenbot.curve.curve_stableswap_liquidity_pool import CurveStableswapPool
from degenbot.erc20_token import Erc20Token
from degenbot.exceptions import ArbitrageError
from degenbot.uniswap.v2_liquidity_pool import LiquidityPool
from degenbot.uniswap.v3_liquidity_pool import V3LiquidityPool

WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
DAI_ADDRESS = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
CURVE_TRIPOOL_ADDRESS = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"


def test_create_arb():
    uniswap_v2_weth_dai_lp = LiquidityPool("0xA478c2975Ab1Ea89e8196811F51A7B7Ade33eB11")
    curve_tripool = CurveStableswapPool("0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7")
    uniswap_v2_weth_usdc_lp = LiquidityPool("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc")

    weth = Erc20Token(WETH_ADDRESS)
    UniswapCurveCycle(
        input_token=weth,
        swap_pools=[uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdc_lp],
        id="test",
        max_input=10 * 10**18,
    )


def test_arb_calculation():
    uniswap_v2_weth_dai_lp = LiquidityPool("0xA478c2975Ab1Ea89e8196811F51A7B7Ade33eB11")
    curve_tripool = CurveStableswapPool("0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7")
    uniswap_v2_weth_usdc_lp = LiquidityPool("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc")

    weth = Erc20Token(WETH_ADDRESS)

    for swap_pools in [
        (uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdc_lp),
        (uniswap_v2_weth_usdc_lp, curve_tripool, uniswap_v2_weth_dai_lp),
    ]:
        arb = UniswapCurveCycle(
            input_token=weth,
            swap_pools=swap_pools,
            id="test",
            max_input=10 * 10**18,
        )
        result = arb.calculate()
        print(result)
