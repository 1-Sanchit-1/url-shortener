"""Operational commands.

python -m app.cli create-admin admin@example.com   # prompts for the password
"""

import argparse
import asyncio
import getpass

from app.core.config import get_settings
from app.core.container import Container
from app.models import Role
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="create a user with the admin role")
    admin.add_argument("email")
    args = parser.parse_args()

    if args.command == "create-admin":
        password = getpass.getpass("Password (min 12 chars): ")
        if len(password) < 12:
            raise SystemExit("password must be at least 12 characters")
        asyncio.run(create_admin(args.email, password))


if __name__ == "__main__":
    main()
