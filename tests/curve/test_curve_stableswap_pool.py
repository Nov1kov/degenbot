import itertools

import degenbot
import pytest
import ujson
from degenbot.curve.abi import CURVE_V1_FACTORY_ABI
from degenbot.curve.curve_stableswap_liquidity_pool import (
    BrokenPool,
    CurveStableswapPool,
    BROKEN_POOLS,
)
from web3 import Web3
from web3.contract import Contract

FRXETH_WETH_CURVE_POOL_ADDRESS = "0x9c3B46C0Ceb5B9e304FCd6D88Fc50f7DD24B31Bc"
CURVE_METAREGISTRY_ADDRESS = "0xF98B45FA17DE75FB1aD0e7aFD971b0ca00e379fC"
CURVE_METAREGISTRY_ABI = ujson.loads(
    """
    [{"name":"CommitNewAdmin","inputs":[{"name":"deadline","type":"uint256","indexed":true},{"name":"admin","type":"address","indexed":true}],"anonymous":false,"type":"event"},{"name":"NewAdmin","inputs":[{"name":"admin","type":"address","indexed":true}],"anonymous":false,"type":"event"},{"stateMutability":"nonpayable","type":"constructor","inputs":[{"name":"_address_provider","type":"address"}],"outputs":[]},{"stateMutability":"nonpayable","type":"function","name":"add_registry_handler","inputs":[{"name":"_registry_handler","type":"address"}],"outputs":[]},{"stateMutability":"nonpayable","type":"function","name":"update_registry_handler","inputs":[{"name":"_index","type":"uint256"},{"name":"_registry_handler","type":"address"}],"outputs":[]},{"stateMutability":"view","type":"function","name":"get_registry_handlers_from_pool","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address[10]"}]},{"stateMutability":"view","type":"function","name":"get_base_registry","inputs":[{"name":"registry_handler","type":"address"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"find_pool_for_coins","inputs":[{"name":"_from","type":"address"},{"name":"_to","type":"address"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"find_pool_for_coins","inputs":[{"name":"_from","type":"address"},{"name":"_to","type":"address"},{"name":"i","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"find_pools_for_coins","inputs":[{"name":"_from","type":"address"},{"name":"_to","type":"address"}],"outputs":[{"name":"","type":"address[]"}]},{"stateMutability":"view","type":"function","name":"get_admin_balances","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_admin_balances","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_balances","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_balances","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_base_pool","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_base_pool","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_coin_indices","inputs":[{"name":"_pool","type":"address"},{"name":"_from","type":"address"},{"name":"_to","type":"address"}],"outputs":[{"name":"","type":"int128"},{"name":"","type":"int128"},{"name":"","type":"bool"}]},{"stateMutability":"view","type":"function","name":"get_coin_indices","inputs":[{"name":"_pool","type":"address"},{"name":"_from","type":"address"},{"name":"_to","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"int128"},{"name":"","type":"int128"},{"name":"","type":"bool"}]},{"stateMutability":"view","type":"function","name":"get_coins","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address[8]"}]},{"stateMutability":"view","type":"function","name":"get_coins","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"address[8]"}]},{"stateMutability":"view","type":"function","name":"get_decimals","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_decimals","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_fees","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[10]"}]},{"stateMutability":"view","type":"function","name":"get_fees","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256[10]"}]},{"stateMutability":"view","type":"function","name":"get_gauge","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_gauge","inputs":[{"name":"_pool","type":"address"},{"name":"gauge_idx","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_gauge","inputs":[{"name":"_pool","type":"address"},{"name":"gauge_idx","type":"uint256"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_gauge_type","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"int128"}]},{"stateMutability":"view","type":"function","name":"get_gauge_type","inputs":[{"name":"_pool","type":"address"},{"name":"gauge_idx","type":"uint256"}],"outputs":[{"name":"","type":"int128"}]},{"stateMutability":"view","type":"function","name":"get_gauge_type","inputs":[{"name":"_pool","type":"address"},{"name":"gauge_idx","type":"uint256"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"int128"}]},{"stateMutability":"view","type":"function","name":"get_lp_token","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_lp_token","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_n_coins","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"get_n_coins","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"get_n_underlying_coins","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"get_n_underlying_coins","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"get_pool_asset_type","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"get_pool_asset_type","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"get_pool_from_lp_token","inputs":[{"name":"_token","type":"address"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_pool_from_lp_token","inputs":[{"name":"_token","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_pool_params","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[20]"}]},{"stateMutability":"view","type":"function","name":"get_pool_params","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256[20]"}]},{"stateMutability":"view","type":"function","name":"get_pool_name","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"string"}]},{"stateMutability":"view","type":"function","name":"get_pool_name","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"string"}]},{"stateMutability":"view","type":"function","name":"get_underlying_balances","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_underlying_balances","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_underlying_coins","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address[8]"}]},{"stateMutability":"view","type":"function","name":"get_underlying_coins","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"address[8]"}]},{"stateMutability":"view","type":"function","name":"get_underlying_decimals","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_underlying_decimals","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256[8]"}]},{"stateMutability":"view","type":"function","name":"get_virtual_price_from_lp_token","inputs":[{"name":"_token","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"get_virtual_price_from_lp_token","inputs":[{"name":"_token","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"is_meta","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"bool"}]},{"stateMutability":"view","type":"function","name":"is_meta","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},{"stateMutability":"view","type":"function","name":"is_registered","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"bool"}]},{"stateMutability":"view","type":"function","name":"is_registered","inputs":[{"name":"_pool","type":"address"},{"name":"_handler_id","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},{"stateMutability":"view","type":"function","name":"pool_count","inputs":[],"outputs":[{"name":"","type":"uint256"}]},{"stateMutability":"view","type":"function","name":"pool_list","inputs":[{"name":"_index","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"address_provider","inputs":[],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"owner","inputs":[],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_registry","inputs":[{"name":"arg0","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"registry_length","inputs":[],"outputs":[{"name":"","type":"uint256"}]}]
    """
)
CURVE_V1_FACTORY_ADDRESS = "0x127db66E7F0b16470Bec194d0f496F9Fa065d0A9"
# -----------------------------------------------------------
# These are unused in favor of the metaregistry
CURVE_REGISTRY_ADDRESS = "0x90E00ACe148ca3b23Ac1bC8C240C2a7Dd9c2d7f5"
CURVE_REGISTRY_ABI = ujson.loads(
    """
    [{"name":"PoolAdded","inputs":[{"name":"pool","type":"address","indexed":true},{"name":"rate_method_id","type":"bytes","indexed":false}],"anonymous":false,"type":"event"},{"name":"PoolRemoved","inputs":[{"name":"pool","type":"address","indexed":true}],"anonymous":false,"type":"event"},{"stateMutability":"nonpayable","type":"constructor","inputs":[{"name":"_address_provider","type":"address"},{"name":"_gauge_controller","type":"address"}],"outputs":[]},{"stateMutability":"view","type":"function","name":"find_pool_for_coins","inputs":[{"name":"_from","type":"address"},{"name":"_to","type":"address"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"find_pool_for_coins","inputs":[{"name":"_from","type":"address"},{"name":"_to","type":"address"},{"name":"i","type":"uint256"}],"outputs":[{"name":"","type":"address"}]},{"stateMutability":"view","type":"function","name":"get_n_coins","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[2]"}],"gas":1521},{"stateMutability":"view","type":"function","name":"get_coins","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address[8]"}],"gas":12102},{"stateMutability":"view","type":"function","name":"get_underlying_coins","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address[8]"}],"gas":12194},{"stateMutability":"view","type":"function","name":"get_decimals","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}],"gas":7874},{"stateMutability":"view","type":"function","name":"get_underlying_decimals","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}],"gas":7966},{"stateMutability":"view","type":"function","name":"get_rates","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}],"gas":36992},{"stateMutability":"view","type":"function","name":"get_gauges","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"address[10]"},{"name":"","type":"int128[10]"}],"gas":20157},{"stateMutability":"view","type":"function","name":"get_balances","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}],"gas":16583},{"stateMutability":"view","type":"function","name":"get_underlying_balances","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}],"gas":162842},{"stateMutability":"view","type":"function","name":"get_virtual_price_from_lp_token","inputs":[{"name":"_token","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"gas":1927},{"stateMutability":"view","type":"function","name":"get_A","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"gas":1045},{"stateMutability":"view","type":"function","name":"get_parameters","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"A","type":"uint256"},{"name":"future_A","type":"uint256"},{"name":"fee","type":"uint256"},{"name":"admin_fee","type":"uint256"},{"name":"future_fee","type":"uint256"},{"name":"future_admin_fee","type":"uint256"},{"name":"future_owner","type":"address"},{"name":"initial_A","type":"uint256"},{"name":"initial_A_time","type":"uint256"},{"name":"future_A_time","type":"uint256"}],"gas":6305},{"stateMutability":"view","type":"function","name":"get_fees","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[2]"}],"gas":1450},{"stateMutability":"view","type":"function","name":"get_admin_balances","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256[8]"}],"gas":36454},{"stateMutability":"view","type":"function","name":"get_coin_indices","inputs":[{"name":"_pool","type":"address"},{"name":"_from","type":"address"},{"name":"_to","type":"address"}],"outputs":[{"name":"","type":"int128"},{"name":"","type":"int128"},{"name":"","type":"bool"}],"gas":27131},{"stateMutability":"view","type":"function","name":"estimate_gas_used","inputs":[{"name":"_pool","type":"address"},{"name":"_from","type":"address"},{"name":"_to","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"gas":32004},{"stateMutability":"view","type":"function","name":"is_meta","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"bool"}],"gas":1900},{"stateMutability":"view","type":"function","name":"get_pool_name","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"string"}],"gas":8323},{"stateMutability":"view","type":"function","name":"get_coin_swap_count","inputs":[{"name":"_coin","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"gas":1951},{"stateMutability":"view","type":"function","name":"get_coin_swap_complement","inputs":[{"name":"_coin","type":"address"},{"name":"_index","type":"uint256"}],"outputs":[{"name":"","type":"address"}],"gas":2090},{"stateMutability":"view","type":"function","name":"get_pool_asset_type","inputs":[{"name":"_pool","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"gas":2011},{"stateMutability":"nonpayable","type":"function","name":"add_pool","inputs":[{"name":"_pool","type":"address"},{"name":"_n_coins","type":"uint256"},{"name":"_lp_token","type":"address"},{"name":"_rate_info","type":"bytes32"},{"name":"_decimals","type":"uint256"},{"name":"_underlying_decimals","type":"uint256"},{"name":"_has_initial_A","type":"bool"},{"name":"_is_v1","type":"bool"},{"name":"_name","type":"string"}],"outputs":[],"gas":61485845},{"stateMutability":"nonpayable","type":"function","name":"add_pool_without_underlying","inputs":[{"name":"_pool","type":"address"},{"name":"_n_coins","type":"uint256"},{"name":"_lp_token","type":"address"},{"name":"_rate_info","type":"bytes32"},{"name":"_decimals","type":"uint256"},{"name":"_use_rates","type":"uint256"},{"name":"_has_initial_A","type":"bool"},{"name":"_is_v1","type":"bool"},{"name":"_name","type":"string"}],"outputs":[],"gas":31306062},{"stateMutability":"nonpayable","type":"function","name":"add_metapool","inputs":[{"name":"_pool","type":"address"},{"name":"_n_coins","type":"uint256"},{"name":"_lp_token","type":"address"},{"name":"_decimals","type":"uint256"},{"name":"_name","type":"string"}],"outputs":[]},{"stateMutability":"nonpayable","type":"function","name":"add_metapool","inputs":[{"name":"_pool","type":"address"},{"name":"_n_coins","type":"uint256"},{"name":"_lp_token","type":"address"},{"name":"_decimals","type":"uint256"},{"name":"_name","type":"string"},{"name":"_base_pool","type":"address"}],"outputs":[]},{"stateMutability":"nonpayable","type":"function","name":"remove_pool","inputs":[{"name":"_pool","type":"address"}],"outputs":[],"gas":779731418758},{"stateMutability":"nonpayable","type":"function","name":"set_pool_gas_estimates","inputs":[{"name":"_addr","type":"address[5]"},{"name":"_amount","type":"uint256[2][5]"}],"outputs":[],"gas":390460},{"stateMutability":"nonpayable","type":"function","name":"set_coin_gas_estimates","inputs":[{"name":"_addr","type":"address[10]"},{"name":"_amount","type":"uint256[10]"}],"outputs":[],"gas":392047},{"stateMutability":"nonpayable","type":"function","name":"set_gas_estimate_contract","inputs":[{"name":"_pool","type":"address"},{"name":"_estimator","type":"address"}],"outputs":[],"gas":72629},{"stateMutability":"nonpayable","type":"function","name":"set_liquidity_gauges","inputs":[{"name":"_pool","type":"address"},{"name":"_liquidity_gauges","type":"address[10]"}],"outputs":[],"gas":400675},{"stateMutability":"nonpayable","type":"function","name":"set_pool_asset_type","inputs":[{"name":"_pool","type":"address"},{"name":"_asset_type","type":"uint256"}],"outputs":[],"gas":72667},{"stateMutability":"nonpayable","type":"function","name":"batch_set_pool_asset_type","inputs":[{"name":"_pools","type":"address[32]"},{"name":"_asset_types","type":"uint256[32]"}],"outputs":[],"gas":1173447},{"stateMutability":"view","type":"function","name":"address_provider","inputs":[],"outputs":[{"name":"","type":"address"}],"gas":2048},{"stateMutability":"view","type":"function","name":"gauge_controller","inputs":[],"outputs":[{"name":"","type":"address"}],"gas":2078},{"stateMutability":"view","type":"function","name":"pool_list","inputs":[{"name":"arg0","type":"uint256"}],"outputs":[{"name":"","type":"address"}],"gas":2217},{"stateMutability":"view","type":"function","name":"pool_count","inputs":[],"outputs":[{"name":"","type":"uint256"}],"gas":2138},{"stateMutability":"view","type":"function","name":"coin_count","inputs":[],"outputs":[{"name":"","type":"uint256"}],"gas":2168},{"stateMutability":"view","type":"function","name":"get_coin","inputs":[{"name":"arg0","type":"uint256"}],"outputs":[{"name":"","type":"address"}],"gas":2307},{"stateMutability":"view","type":"function","name":"get_pool_from_lp_token","inputs":[{"name":"arg0","type":"address"}],"outputs":[{"name":"","type":"address"}],"gas":2443},{"stateMutability":"view","type":"function","name":"get_lp_token","inputs":[{"name":"arg0","type":"address"}],"outputs":[{"name":"","type":"address"}],"gas":2473},{"stateMutability":"view","type":"function","name":"last_updated","inputs":[],"outputs":[{"name":"","type":"uint256"}],"gas":2288}]
    """
)
CURVE_POOLINFO_ADDRESS = "0xe64608E223433E8a03a1DaaeFD8Cb638C14B552C"
CURVE_POOLINFO_ABI = ujson.loads(
    """
    [{"outputs":[],"inputs":[{"type":"address","name":"_provider"}],"stateMutability":"nonpayable","type":"constructor"},{"name":"get_pool_coins","outputs":[{"type":"address[8]","name":"coins"},{"type":"address[8]","name":"underlying_coins"},{"type":"uint256[8]","name":"decimals"},{"type":"uint256[8]","name":"underlying_decimals"}],"inputs":[{"type":"address","name":"_pool"}],"stateMutability":"view","type":"function","gas":15876},{"name":"get_pool_info","outputs":[{"type":"uint256[8]","name":"balances"},{"type":"uint256[8]","name":"underlying_balances"},{"type":"uint256[8]","name":"decimals"},{"type":"uint256[8]","name":"underlying_decimals"},{"type":"uint256[8]","name":"rates"},{"type":"address","name":"lp_token"},{"type":"tuple","name":"params","components":[{"type":"uint256","name":"A"},{"type":"uint256","name":"future_A"},{"type":"uint256","name":"fee"},{"type":"uint256","name":"admin_fee"},{"type":"uint256","name":"future_fee"},{"type":"uint256","name":"future_admin_fee"},{"type":"address","name":"future_owner"},{"type":"uint256","name":"initial_A"},{"type":"uint256","name":"initial_A_time"},{"type":"uint256","name":"future_A_time"}]}],"inputs":[{"type":"address","name":"_pool"}],"stateMutability":"view","type":"function","gas":35142},{"name":"address_provider","outputs":[{"type":"address","name":""}],"inputs":[],"stateMutability":"view","type":"function","gas":1121}]
    """
)
# -----------------------------------------------------------


@pytest.fixture()
def metaregistry(local_web3_ethereum_full: Web3) -> Contract:
    return local_web3_ethereum_full.eth.contract(
        address=CURVE_METAREGISTRY_ADDRESS, abi=CURVE_METAREGISTRY_ABI
    )


def _test_balances(lp: CurveStableswapPool, metaregistry: Contract):
    contract_balances = metaregistry.functions.get_balances(lp.address).call()[: len(lp.tokens)]
    assert contract_balances == lp.balances


def _test_calculations(lp: CurveStableswapPool):
    for token_in_index, token_out_index in itertools.permutations(range(len(lp.tokens)), 2):
        token_in = lp.tokens[token_in_index]
        token_out = lp.tokens[token_out_index]

        for amount_multiplier in [0.01, 0.05, 0.25]:
            amount = int(amount_multiplier * lp.balances[lp.tokens.index(token_in)])

            if amount == 0:
                # Skip empty pools
                continue

            print(f"Swapping {amount} {token_in} for {token_out}")

            if lp.address == "0xDeBF20617708857ebe4F679508E7b7863a8A8EeE":
                dynamic_fee = True
            else:
                dynamic_fee = False

            calc_amount = lp.calculate_tokens_out_from_tokens_in(
                token_in=token_in,
                token_out=token_out,
                token_in_quantity=amount,
                dynamic_fee=dynamic_fee,
            )

            contract_amount = lp._w3_contract.functions.get_dy(
                token_in_index,
                token_out_index,
                amount,
            ).call()

            assert calc_amount == contract_amount


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


def test_3pool(local_web3_ethereum_full: Web3, metaregistry: Contract):
    degenbot.set_web3(local_web3_ethereum_full)
    tripool = CurveStableswapPool("0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7")

    _test_balances(tripool, metaregistry)
    _test_calculations(tripool)


def test_base_registry_pools(local_web3_ethereum_full: Web3, metaregistry: Contract):
    """
    Test the custom pools deployed by Curve
    """
    degenbot.set_web3(local_web3_ethereum_full)

    registry: Contract = local_web3_ethereum_full.eth.contract(
        address=CURVE_REGISTRY_ADDRESS, abi=CURVE_REGISTRY_ABI
    )
    pool_count = registry.functions.pool_count().call()

    for pool_id in range(pool_count):
        # Get the address and pool info
        pool_address = registry.functions.pool_list(pool_id).call()
        print(f"{pool_id}: {pool_address=}")

        lp = CurveStableswapPool(address=pool_address)
        _test_balances(lp, metaregistry)
        _test_calculations(lp)


def test_single_pool(local_web3_ethereum_archive: Web3, metaregistry: Contract):
    degenbot.set_web3(local_web3_ethereum_archive)

    POOL_ADDRESS = "0x84997FAFC913f1613F51Bb0E2b5854222900514B"

    try:
        lp = CurveStableswapPool(address=POOL_ADDRESS)
    except BrokenPool:
        pass
    else:
        _test_balances(lp, metaregistry)
        _test_calculations(lp)


def test_factory_stableswap_pools(local_web3_ethereum_full: Web3, metaregistry: Contract):
    """
    Test the user-deployed pools deployed by the factory
    """
    degenbot.set_web3(local_web3_ethereum_full)

    stableswap_factory: Contract = local_web3_ethereum_full.eth.contract(
        address=CURVE_V1_FACTORY_ADDRESS, abi=CURVE_V1_FACTORY_ABI
    )
    pool_count = stableswap_factory.functions.pool_count().call()

    for pool_id in range(pool_count):
        pool_address = stableswap_factory.functions.pool_list(pool_id).call()

        if pool_address in BROKEN_POOLS:
            continue

        try:
            lp = CurveStableswapPool(address=pool_address)
        except Exception:
            print(f"Cannot build pool {pool_address}")
            raise
        except BrokenPool:
            pass
        else:
            _test_balances(lp, metaregistry)
            _test_calculations(lp)


def test_all_registered_pools(local_web3_ethereum_full: Web3, metaregistry: Contract):
    degenbot.set_web3(local_web3_ethereum_full)

    pool_count = metaregistry.functions.pool_count().call()

    for pool_id in range(pool_count):
        # Get the address and pool info
        pool_address = metaregistry.functions.pool_list(pool_id).call()
        print(f"{pool_id}: {pool_address=}")

        # # pool_balances,
        # # pool_underlying_balances,
        # pool_decimals = metaregistry.functions.get_pool_decimals(pool_address).call()
        # # pool_underlying_decimals,

        # TODO: investigate errors on these pools

        # 810: pool_address='0x7F86Bf177Dd4F3494b841a37e810A34dD56c829B'
        # 811: pool_address='0xf5f5B97624542D72A9E06f04804Bf81baA15e2B4'
        # 812: pool_address='0x2889302a794dA87fBF1D6Db415C1492194663D13'
        # 813: pool_address='0x5426178799ee0a0181A89b4f57eFddfAb49941Ec'
        # 814: pool_address='0x4eBdF703948ddCEA3B11f675B4D1Fba9d2414A14'
        # 815: pool_address='0x9847a74fB7C3c4362220f616E15b83A58527F7E4'
        # 816: pool_address='0xdcafD1914afDBC5788B701F47283CaeEAa5FBAed'
        # 817: pool_address='0x05CA1ff6fF45e55906c86Ad0d3FB2EbFaE9E0891'
        # 818: pool_address='0x037164C912f9733A0973B18EE339FBeF66cfd2C2'
        # 819: pool_address='0x3921e2cb3Ac3bC009Fa4ec5Ea1ee0bc7FA4Be4C1'
        # 820: pool_address='0x38AB39c82BE45f660AFa4A74E85dAd4b4aDd0492'
        # 821: pool_address='0x86bF09aCB47AB31686bE413d614E9ded3666a1d3'
        # 822: pool_address='0x50120e3348287C6d001E455f5b00FeA07A875541'
        # 823: pool_address='0x6A62EE3e5c4b412Cd9167D3aFd5E481e1E30715a'
        # 824: pool_address='0x2570f1bD5D2735314FC102eb12Fc1aFe9e6E7193'
        # 825: pool_address='0x56aEFfd9935ACabF21543701212d67aD529F7f2e'
        # 826: pool_address='0x954313005C56b555bdC41B84D6c63B69049d7847'
        # 827: pool_address='0x1Ac76b6e2926ff475969d22a2258449a4600E006'
        # 828: pool_address='0xC7DE47b9Ca2Fc753D6a2F167D8b3e19c6D18b19a'
        # 829: pool_address='0x5b3BA844b3859f56524e99Ae54857b36c8Ae3eFE'
        # 830: pool_address='0x8e2b641271544300e59d14E27520DEA204056D66'
        # 831: pool_address='0xDB6925eA42897ca786a045B252D95aA7370f44b4'
        # 832: pool_address='0x4D1941a887eC788F059b3bfcC8eE1E97b968825B'
        # 833: pool_address='0x35B269Fe0106d3645d9780C5aaD97C8eb8041c40'
        # 834: pool_address='0x84CeCB5525c6B1C20070E742da870062E84Da178'
        # 835: pool_address='0x1830c553dC76d3447B69b7B0dC19CF9e3c739C78'

        try:
            pool_params: list = metaregistry.functions.get_pool_params(pool_address).call()
        except Exception:
            print(f"Error getting params from pool {pool_address}")
            continue

        if all(param == 0 for param in pool_params[1:]):
            try:
                CurveStableswapPool(address=pool_address)
            except Exception:
                print(f"Building pool failure: {pool_address=}")
        else:
            (
                pool_a,
                pool_d,
                pool_gamma,
                # pool_extra_profit,
                # pool_fee_gamma,
                # pool_adj_step,
                # pool_ma_half_time,
                *_,
            ) = pool_params[1:]
            print(f"CryptoPool detected: {pool_a=}, {pool_d=}, {pool_gamma=}")
