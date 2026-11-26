"""PostgreSQL custom-format backups and restore into an explicitly empty database."""

import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "restore"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--url-env", default="DATABASE_URL", help="Environment variable containing the database URL")
    args = parser.parse_args()
    try:
        url = make_url(os.environ[args.url_env])
        if not url.drivername.startswith("postgresql"):
            raise ValueError("PostgreSQL URL required")
        archive = args.archive.resolve()
        tool = "pg_dump" if args.operation == "backup" else "pg_restore"
        binary = shutil.which(tool)
        if not binary:
            raise ValueError("Install PostgreSQL client utilities first")
        if args.operation == "backup" and archive.exists():
            raise ValueError("Archive destination must be a new file")
        if args.operation == "restore" and (not archive.is_file() or args.url_env == "DATABASE_URL"):
            raise ValueError("Restore requires an existing archive and an explicit separate --url-env")
        connection_args = {
            "host": url.host or "localhost",
            "port": url.port or 5432,
            "user": url.username,
            "dbname": url.database,
            "connect_timeout": 10,
        }
        if args.operation == "restore":
            with psycopg.connect(**connection_args, password=url.password) as connection:
                if connection.execute(
                    "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname NOT IN "
                    "('pg_catalog', 'information_schema') LIMIT 1"
                ).fetchone():
                    raise ValueError("Restore target must have no user tables")
        environment = os.environ.copy()
        if url.password:
            environment["PGPASSWORD"] = url.password
        arguments = [
            binary,
            "--host",
            connection_args["host"],
            "--port",
            str(connection_args["port"]),
            "--username",
            url.username or "postgres",
            "--dbname",
            url.database or "postgres",
            "--no-password",
        ]
        if args.operation == "backup":
            archive.parent.mkdir(parents=True, exist_ok=True)
            arguments.extend(["--format=custom", "--file", str(archive)])
        else:
            arguments.extend(["--no-owner", "--exit-on-error", str(archive)])
        started = time.perf_counter()
        # Database URLs and tool stderr can contain private infrastructure details: do not print them.
        subprocess.run(
            arguments, env=environment, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600
        )
        print(
            f"{args.operation.capitalize()} completed in {time.perf_counter() - started:.2f}s; "
            "run integrity and row-count evaluation on the separate restore target."
        )
    except (KeyError, ValueError, OSError, subprocess.SubprocessError, psycopg.Error):
        parser.exit(
            1,
            "Database operation failed. Check PostgreSQL client availability, target isolation, permissions and connectivity.\n",
        )


if __name__ == "__main__":
    main()
