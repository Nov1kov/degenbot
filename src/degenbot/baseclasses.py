from abc import ABC, abstractmethod
from eth_typing import ChecksumAddress


class ArbitrageHelper(ABC):
    pass


class HelperManager(ABC):
    """
    An abstract base class for managers that generate, track and distribute various helper classes
    """

    pass


class AbstractPoolUpdate:
    ...


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

    # All abstract methods below must be implemented by derived classes
    @abstractmethod
    def calculate_tokens_out_from_tokens_in(
        self, token_in, token_out, token_in_quantity, override_state
    ):
        ...

    @abstractmethod
    def auto_update(self):
        ...

    @abstractmethod
    def external_update(self, update):
        ...


class TokenHelper(ABC):
    pass


class TransactionHelper(ABC):
    pass
