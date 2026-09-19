"""Operational commands.

python -m app.cli create-admin admin@example.com   # prompts for the password
"""

import argparse
import asyncio
import getpass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, text

from app.core.config import get_settings
from app.core.container import Container
from app.models import RefreshToken, Role
from app.services.auth import AuthService


async def create_admin(email: str, password: str) -> None:
    container = await Container.create(get_settings())
    try:
        async with container.sessionmaker() as session:
            user = await AuthService(session, container.settings).register(
                email, password, role=Role.ADMIN
            )
            print(f"created admin user id={user.id} email={user.email}")
    finally:
        await container.aclose()


async def purge_refresh_tokens() -> None:
    container = await Container.create(get_settings())
    try:
        async with container.sessionmaker() as session:
            result = await session.execute(
                delete(RefreshToken).where(RefreshToken.expires_at < datetime.now(UTC))
            )
            await session.commit()
            print(f"deleted {result.rowcount} expired refresh tokens")  # type: ignore[attr-defined]
    finally:
        await container.aclose()


async def purge_clicks(days: int, batch_size: int = 10_000) -> None:
    """Delete raw click events older than ``days`` in small batches.

    Small batches keep each transaction and its WAL burst short, so ingestion isn't
    blocked. The time filter is served by the BRIN index on occurred_at. Hourly
    rollups are kept, so historical series stay available.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    container = await Container.create(get_settings())
    total = 0
    try:
        while True:
            async with container.sessionmaker() as session:
                result = await session.execute(
                    text(
                        "DELETE FROM click_events WHERE id IN ("
                        "  SELECT id FROM click_events WHERE occurred_at < :cutoff LIMIT :n)"
                    ),
                    {"cutoff": cutoff, "n": batch_size},
                )
                await session.commit()
            deleted = result.rowcount  # type: ignore[attr-defined]
            total += deleted
            if deleted < batch_size:
                break
        print(f"deleted {total} click events older than {cutoff.isoformat()}")
    finally:
        await container.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="create a user with the admin role")
    admin.add_argument("email")
    commands.add_parser("purge-refresh-tokens", help="delete expired refresh tokens")
    purge = commands.add_parser("purge-clicks", help="delete raw click events past retention")
    purge.add_argument("--days", type=int, required=True)
    args = parser.parse_args()

    if args.command == "create-admin":
        password = getpass.getpass("Password (min 12 chars): ")
        if len(password) < 12:
            raise SystemExit("password must be at least 12 characters")
        asyncio.run(create_admin(args.email, password))
    elif args.command == "purge-refresh-tokens":
        asyncio.run(purge_refresh_tokens())
    elif args.command == "purge-clicks":
        asyncio.run(purge_clicks(args.days))


if __name__ == "__main__":
    main()
