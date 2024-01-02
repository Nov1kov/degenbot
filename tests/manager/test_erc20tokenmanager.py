import web3
from degenbot import set_web3
from degenbot.manager import Erc20TokenHelperManager
from degenbot.registry import AllTokens
from eth_utils.address import to_checksum_address

WETH_ADDRESS = to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
WBTC_ADDRESS = to_checksum_address("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599")


def test_get_erc20tokens(local_ethereum_archive_node_web3: web3.Web3):
    set_web3(local_ethereum_archive_node_web3)
    token_manager = Erc20TokenHelperManager(chain_id=local_ethereum_archive_node_web3.eth.chain_id)
    token_registry = AllTokens(chain_id=local_ethereum_archive_node_web3.eth.chain_id)

    token_registry.tokens.clear()
    token_manager._erc20tokens.clear()

    weth = token_manager.get_erc20token(address=WETH_ADDRESS)
    assert weth.symbol == "WETH"
    assert weth.address == WETH_ADDRESS
    assert token_registry.get(weth.address) is weth

    wbtc = token_manager.get_erc20token(address=WBTC_ADDRESS)
    assert wbtc.symbol == "WBTC"
    assert wbtc.address == WBTC_ADDRESS
    assert token_registry.get(wbtc.address) is wbtc
