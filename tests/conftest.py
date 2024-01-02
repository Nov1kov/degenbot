# pragma: no cover

import pytest
import dotenv
import web3
import degenbot

ARCHIVE_NODE_HTTP_URI = "http://localhost:8545"
ARCHIVE_NODE_WS_URI = "ws://localhost:8546"


@pytest.fixture(scope="session")
def load_env() -> dict:
    env_file = dotenv.find_dotenv("tests.env")
    return dotenv.dotenv_values(env_file)


# Set up a web3 connection to local archive node
@pytest.fixture(scope="session")
def local_ethereum_archive_node_web3() -> web3.Web3:
    w3 = web3.Web3(web3.WebsocketProvider(ARCHIVE_NODE_WS_URI))
    return w3


@pytest.fixture(scope="function", autouse=True)
def clear_degenbot_state() -> None:
    # Clear shared state dictionaries prior to each new test (activated on every test by autouse=True).
    # These dictionaries store module-level state, which will corrupt sequential tests if not reclearedset
    print("Resetting shared degenbot state")

    degenbot.registry.all_pools._all_pools.clear()
    degenbot.registry.all_tokens._all_tokens.clear()
    degenbot.UniswapV2LiquidityPoolManager._state.clear()
    degenbot.UniswapV3LiquidityPoolManager._state.clear()


@pytest.fixture(scope="function")
def fork_from_archive() -> degenbot.AnvilFork:
    fork = degenbot.AnvilFork(fork_url=ARCHIVE_NODE_HTTP_URI)
    yield fork
