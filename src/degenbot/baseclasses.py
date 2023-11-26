from abc import ABC
from eth_typing import ChecksumAddress


class ArbitrageHelper(ABC):
    pass


class HelperManager(ABC):
    """
    An abstract base class for managers that generate, track and distribute various helper classes
    """

    pass


class PoolHelper(ABC):
    address: ChecksumAddress
    name: str

    def __eq__(self, other) -> bool:
        if issubclass(type(other), PoolHelper):
            return self.address == other.address
        elif isinstance(other, str):
            return self.address.lower() == other.lower()
        else:
            raise NotImplementedError

    def __hash__(self):
        return hash(self.address)

    def __str__(self):
        return self.name


class TokenHelper(ABC):
    pass


class TransactionHelper(ABC):
    pass
