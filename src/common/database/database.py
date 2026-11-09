from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session


class Database:
    """A connected database: the engine plus a way to run work in a transaction.

    Built by DatabaseFactory; nothing else creates engines. Tables come from
    migrations (schema_migrations.upgrade_schema_to_latest), never from here.
    """

    def __init__(self, engine: Engine):
        self._engine = engine
        # expire_on_commit=False so objects a repository returns can still be
        # read after its transaction has ended
        self._session_maker = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    @property
    def engine(self) -> Engine:
        return self._engine

    # Everything inside the block is one transaction: committed if the block
    # finishes, rolled back if it raises.
    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self._session_maker()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self._engine.dispose()
