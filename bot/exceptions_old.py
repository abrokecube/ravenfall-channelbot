
class OutOfStockError(Exception):
    def __init__(self, amount_needed: int, amount_in_stock: int, message: str = "Out of stock") -> None:
        self.message: str = message
        self.amount_needed: int = amount_needed
        self.amount_in_stock: int = amount_in_stock
        super().__init__(message)
