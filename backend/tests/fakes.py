"""Shared test doubles that stand in for database infrastructure.

Nothing here opens a connection: the fakes only record how the code under
test drives them.
"""


class FakeTransaction:
    """Async context manager mimicking ``async_sessionmaker.begin()``.

    Attributes:
        _factory: The factory that opened this transaction; entering and
            exiting are counted on it.
    """

    def __init__(self, factory: "FakeSessionFactory") -> None:
        """Bind the transaction to the factory that counts it.

        Args:
            factory: The fake session factory that opened this transaction.
        """
        self._factory = factory

    async def __aenter__(self) -> object:
        """Open the transaction and hand out a placeholder session.

        Returns:
            A fresh placeholder object standing in for an ``AsyncSession``.
        """
        db = object()
        self._factory.sessions.append(db)
        self._factory.entered += 1
        return db

    async def __aexit__(self, *_: object) -> bool:
        """Close the transaction without suppressing any exception.

        Returns:
            ``False``, so an exception raised inside the block propagates.
        """
        self._factory.exited += 1
        return False


class FakeSessionFactory:
    """Session factory whose transactions need no database.

    Attributes:
        entered: How many transactions were opened.
        exited: How many transactions were closed.
        sessions: The placeholder session handed out by each transaction, in
            order, so a test can tell independent transactions apart.
    """

    def __init__(self) -> None:
        """Start with no transactions recorded."""
        self.entered = 0
        self.exited = 0
        self.sessions: list[object] = []

    def begin(self) -> FakeTransaction:
        """Open a new fake transaction.

        Returns:
            A transaction that records itself on this factory.
        """
        return FakeTransaction(self)
