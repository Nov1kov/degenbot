import pickle
import asyncio
import concurrent.futures
import multiprocessing
from degenbot.exceptions import ArbitrageError
from degenbot.arbitrage.uniswap_curve_cycle import UniswapCurveCycle
from degenbot.curve.curve_stableswap_liquidity_pool import CurveStableswapPool
from degenbot.erc20_token import Erc20Token
from degenbot.uniswap.v2_liquidity_pool import LiquidityPool, UniswapV2PoolState
import time

WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
DAI_ADDRESS = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT_ADDRESS = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
CURVE_TRIPOOL_ADDRESS = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"
UNISWAP_V2_WETH_DAI_ADDRESS = "0xA478c2975Ab1Ea89e8196811F51A7B7Ade33eB11"
UNISWAP_V2_WETH_USDC_ADDRESS = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
UNISWAP_V2_WETH_USDT_ADDRESS = "0x0d4a11d5EEaaC28EC3F61d100daF4d40471f1852"
FAKE_ADDRESS = "0x6942000000000000000000000000000000000000"


def test_create_arb():
    uniswap_v2_weth_dai_lp = LiquidityPool(UNISWAP_V2_WETH_DAI_ADDRESS)
    curve_tripool = CurveStableswapPool(CURVE_TRIPOOL_ADDRESS)
    uniswap_v2_weth_usdc_lp = LiquidityPool(UNISWAP_V2_WETH_USDC_ADDRESS)

    weth = Erc20Token(WETH_ADDRESS)
    UniswapCurveCycle(
        input_token=weth,
        swap_pools=[uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdc_lp],
        id="test",
        max_input=10 * 10**18,
    )


def test_pickle_arb():
    uniswap_v2_weth_dai_lp = LiquidityPool(UNISWAP_V2_WETH_DAI_ADDRESS)
    curve_tripool = CurveStableswapPool(CURVE_TRIPOOL_ADDRESS)
    uniswap_v2_weth_usdc_lp = LiquidityPool(UNISWAP_V2_WETH_USDC_ADDRESS)

    weth = Erc20Token(WETH_ADDRESS)
    arb = UniswapCurveCycle(
        input_token=weth,
        swap_pools=[uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdc_lp],
        id="test",
        max_input=10 * 10**18,
    )
    pickle.dumps(arb)


def test_arb_calculation():
    curve_tripool = CurveStableswapPool(CURVE_TRIPOOL_ADDRESS)
    uniswap_v2_weth_dai_lp = LiquidityPool(UNISWAP_V2_WETH_DAI_ADDRESS)
    uniswap_v2_weth_usdc_lp = LiquidityPool(UNISWAP_V2_WETH_USDC_ADDRESS)
    uniswap_v2_weth_usdt_lp = LiquidityPool(UNISWAP_V2_WETH_USDT_ADDRESS)

    weth = Erc20Token(WETH_ADDRESS)

    for swap_pools in [
        (uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdc_lp),
        (uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdt_lp),
        (uniswap_v2_weth_usdc_lp, curve_tripool, uniswap_v2_weth_dai_lp),
        (uniswap_v2_weth_usdc_lp, curve_tripool, uniswap_v2_weth_usdt_lp),
        (uniswap_v2_weth_usdt_lp, curve_tripool, uniswap_v2_weth_dai_lp),
        (uniswap_v2_weth_usdt_lp, curve_tripool, uniswap_v2_weth_usdc_lp),
    ]:
        try:
            arb = UniswapCurveCycle(
                input_token=weth,
                swap_pools=swap_pools,
                id="test",
                max_input=10 * 10**18,
            )
            arb.calculate()
        except ArbitrageError:
            pass


def test_arb_payload_encoding():
    curve_tripool = CurveStableswapPool(CURVE_TRIPOOL_ADDRESS)
    uniswap_v2_weth_dai_lp = LiquidityPool(UNISWAP_V2_WETH_DAI_ADDRESS)
    uniswap_v2_weth_usdc_lp = LiquidityPool(UNISWAP_V2_WETH_USDC_ADDRESS)
    uniswap_v2_weth_usdt_lp = LiquidityPool(UNISWAP_V2_WETH_USDT_ADDRESS)

    weth = Erc20Token(WETH_ADDRESS)

    # set up overrides for a profitable arbitrage condition
    v2_weth_dai_state_override = UniswapV2PoolState(
        pool=uniswap_v2_weth_dai_lp,
        reserves_token0=7154631418308101780013056,  # DAI <----- overridden, added 10% to DAI supply
        reserves_token1=2641882268814772168174,  # WETH
    )
    v2_weth_usdc_lp_state_override = UniswapV2PoolState(
        pool=uniswap_v2_weth_usdc_lp,
        reserves_token0=51264330493455,  # USDC
        reserves_token1=20822226989581225186276,  # WETH
    )
    v2_weth_usdt_lp_state_override = UniswapV2PoolState(
        pool=uniswap_v2_weth_usdt_lp,
        reserves_token0=33451964234532476269546,  # WETH
        reserves_token1=82374477120833,  # USDT
    )

    overrides = [
        (uniswap_v2_weth_dai_lp, v2_weth_dai_state_override),
        (uniswap_v2_weth_usdc_lp, v2_weth_usdc_lp_state_override),
        (uniswap_v2_weth_usdt_lp, v2_weth_usdt_lp_state_override),
    ]

    for swap_pools in [
        (uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdc_lp),
        (uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdt_lp),
        # (uniswap_v2_weth_usdc_lp, curve_tripool, uniswap_v2_weth_dai_lp),
        # (uniswap_v2_weth_usdc_lp, curve_tripool, uniswap_v2_weth_usdt_lp),
        # (uniswap_v2_weth_usdt_lp, curve_tripool, uniswap_v2_weth_dai_lp),
        # (uniswap_v2_weth_usdt_lp, curve_tripool, uniswap_v2_weth_usdc_lp),
    ]:
        arb = UniswapCurveCycle(
            input_token=weth,
            swap_pools=swap_pools,
            id="test",
            max_input=10 * 10**18,
        )

        try:
            calc_result = arb.calculate(override_state=overrides)
        except ArbitrageError:
            raise
        else:
            arb.generate_payloads(
                from_address=FAKE_ADDRESS,
                swap_amount=calc_result.input_amount,
                pool_swap_amounts=calc_result.swap_amounts,
            )


async def test_process_pool_calculation() -> None:
    start = time.perf_counter()

    weth = Erc20Token(WETH_ADDRESS)
    curve_tripool = CurveStableswapPool(CURVE_TRIPOOL_ADDRESS)
    uniswap_v2_weth_dai_lp = LiquidityPool(UNISWAP_V2_WETH_DAI_ADDRESS)
    uniswap_v2_weth_usdc_lp = LiquidityPool(UNISWAP_V2_WETH_USDC_ADDRESS)
    uniswap_v2_weth_usdt_lp = LiquidityPool(UNISWAP_V2_WETH_USDT_ADDRESS)

    # Reserves taken from block 19050173
    # ---
    #
    # DAI-USDC-USDT (CurveStable, 0.01%)
    #     • Token 0: DAI - Reserves: 42217927126053167268106015
    #     • Token 1: USDC - Reserves: 41857454785332
    #     • Token 2: USDT - Reserves: 116155337005450
    # DAI-WETH (V2, 0.30%)
    #     • Token 0: DAI - Reserves: 6504210380280092514247627
    #     • Token 1: WETH - Reserves: 2641882268814772168174
    # USDC-WETH (V2, 0.30%)
    #     • Token 0: USDC - Reserves: 51264330493455
    #     • Token 1: WETH - Reserves: 20822226989581225186276
    # WETH-USDT (V2, 0.30%)
    #     • Token 0: WETH - Reserves: 33451964234532476269546
    #     • Token 1: USDT - Reserves: 82374477120833

    # set up overrides for a profitable arbitrage condition
    v2_weth_dai_state_override = UniswapV2PoolState(
        pool=uniswap_v2_weth_dai_lp,
        reserves_token0=7154631418308101780013056,  # DAI <----- overridden, added 10% to DAI supply
        reserves_token1=2641882268814772168174,  # WETH
    )
    v2_weth_usdc_lp_state_override = UniswapV2PoolState(
        pool=uniswap_v2_weth_usdc_lp,
        reserves_token0=51264330493455,  # USDC
        reserves_token1=20822226989581225186276,  # WETH
    )
    v2_weth_usdt_lp_state_override = UniswapV2PoolState(
        pool=uniswap_v2_weth_usdt_lp,
        reserves_token0=33451964234532476269546,  # WETH
        reserves_token1=82374477120833,  # USDT
    )

    overrides = [
        (uniswap_v2_weth_dai_lp, v2_weth_dai_state_override),
        (uniswap_v2_weth_usdc_lp, v2_weth_usdc_lp_state_override),
        (uniswap_v2_weth_usdt_lp, v2_weth_usdt_lp_state_override),
    ]

    with concurrent.futures.ProcessPoolExecutor(
        mp_context=multiprocessing.get_context("spawn"),
    ) as executor:
        for swap_pools in [
            (uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdc_lp),
            (uniswap_v2_weth_dai_lp, curve_tripool, uniswap_v2_weth_usdt_lp),
            # (uniswap_v2_weth_usdc_lp, curve_tripool, uniswap_v2_weth_dai_lp),
            # (uniswap_v2_weth_usdc_lp, curve_tripool, uniswap_v2_weth_usdt_lp),
            # (uniswap_v2_weth_usdt_lp, curve_tripool, uniswap_v2_weth_dai_lp),
            # (uniswap_v2_weth_usdt_lp, curve_tripool, uniswap_v2_weth_usdc_lp),
        ]:
            arb = UniswapCurveCycle(
                input_token=weth,
                swap_pools=swap_pools,
                id="test",
                max_input=10 * 10**18,
            )

            future = await arb.calculate_with_pool(executor=executor, override_state=overrides)
            result = await future
            assert result

        # Saturate the process pool executor with multiple calculations.
        # Should reveal cases of excessive latency.
        _NUM_FUTURES = 512
        calculation_futures = []
        for _ in range(_NUM_FUTURES):
            calculation_futures.append(
                await arb.calculate_with_pool(
                    executor=executor,
                    override_state=overrides,
                )
            )

        assert len(calculation_futures) == _NUM_FUTURES
        for i, task in enumerate(asyncio.as_completed(calculation_futures)):
            await task
            print(f"Completed process_pool calc #{i}, {time.perf_counter()-start:.2f}s since start")
        print(f"Completed {_NUM_FUTURES} calculations in {time.perf_counter() - start:.1f}s")
