"""Operational commands.

python -m app.cli create-admin admin@example.com   # prompts for the password
"""

import argparse
import asyncio
import getpass
from datetime import UTC, datetime

from sqlalchemy import delete

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


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="create a user with the admin role")
    admin.add_argument("email")
    commands.add_parser("purge-refresh-tokens", help="delete expired refresh tokens")
    args = parser.parse_args()

    if args.command == "create-admin":
        password = getpass.getpass("Password (min 12 chars): ")
        if len(password) < 12:
            raise SystemExit("password must be at least 12 characters")
        asyncio.run(create_admin(args.email, password))
    elif args.command == "purge-refresh-tokens":
        asyncio.run(purge_refresh_tokens())


if __name__ == "__main__":
    main()
