"""Public metadata-store API for trusted GAARD extensions.

Extensions should import metadata sessions and the shared declarative base from
this module instead of depending on the admin database implementation.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from gaard_api.admin.database import create_session
from gaard_api.admin.models import (
    Base,
    DatasourceConnector,
    DuckDBFileImport,
    DuckDBFileMaterialization,
    DuckDBFileRelation,
    DuckDBFileWarning,
)

MetadataModelBase = Base


def create_metadata_session() -> Session:
    """Return a session connected to the configured GAARD metadata store."""

    return create_session()


@contextmanager
def metadata_session() -> Iterator[Session]:
    """Open a transactional metadata session and roll back on failure."""

    session = create_metadata_session()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "DatasourceConnector",
    "DuckDBFileImport",
    "DuckDBFileMaterialization",
    "DuckDBFileRelation",
    "DuckDBFileWarning",
    "MetadataModelBase",
    "create_metadata_session",
    "metadata_session",
]
