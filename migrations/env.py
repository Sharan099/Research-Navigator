"""Alembic entry point; credentials come from server configuration, never this file."""

from alembic import context

from backend.app.models import Base


def run(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    from backend.app.config import Settings
    from backend.app.database import create_database_engine, single_instance

    engine = create_database_engine(Settings().database_url)
    with single_instance(engine):
        with engine.connect() as connection:
            sqlite = connection.dialect.name == "sqlite"
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                run(connection)
                if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                    raise RuntimeError("Migration violated foreign-key integrity")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if sqlite:
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    connection.commit()
    engine.dispose()
