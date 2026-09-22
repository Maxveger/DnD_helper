"""Small validation boundary shared by the studio and its model adapter."""


class GameError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise GameError(message)
