class BaseItemSendError(Exception):
    def __init__(self, message: str, items_sent: int = 0):
        super().__init__(message)
        self.message: str = message
        self.items_sent: int = items_sent


class CouldNotSendMessageError(BaseItemSendError):
    pass


class CouldNotSendItemsError(BaseItemSendError):
    pass


class OutOfItemsError(BaseItemSendError):
    pass


class PartialSendError(BaseItemSendError):
    pass


class ItemNotFoundError(BaseItemSendError):
    pass


class RecipientNotFoundError(BaseItemSendError):
    pass


class UnexpectedResponseError(BaseItemSendError):
    pass
