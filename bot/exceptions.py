
class OutOfStockError(Exception):
    def __init__(self, amount_needed: int, amount_in_stock: int, message: str = "Out of stock"):
        self.message = message
        self.amount_needed = amount_needed
        self.amount_in_stock = amount_in_stock
        super().__init__(message)
