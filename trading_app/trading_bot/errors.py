class BrokerError(Exception):
    def __init__(self, message: str, original_code: str | None = None) -> None:
        self.original_code = original_code
        super().__init__(message)


class InsufficientFunds(BrokerError):
    pass


class InvalidSymbol(BrokerError):
    pass


class RateLimited(BrokerError):
    pass


class ConnectionLost(BrokerError):
    pass


class OrderRejected(BrokerError):
    pass


ERROR_CODE_MAP: dict[str, type[BrokerError]] = {
    "InsufficientFunds": InsufficientFunds,
    "InvalidSymbol": InvalidSymbol,
    "RateLimited": RateLimited,
    "ConnectionLost": ConnectionLost,
    "OrderRejected": OrderRejected,
}
