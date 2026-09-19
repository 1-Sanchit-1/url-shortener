from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import Request, Response

from app.api.deps import ContainerDep, PrincipalDep
from app.core.container import Container
from app.core.errors import RateLimitedError
from app.ratelimit.policy import RateLimitPolicy

PolicyName = Literal["redirect", "auth", "write", "read"]


def _policy(container: Container, name: PolicyName) -> RateLimitPolicy:
    policy: RateLimitPolicy = getattr(container.settings, f"rate_limit_{name}")
    return policy


async def _enforce(
    container: Container,
    name: PolicyName,
    identity: str,
    policy: RateLimitPolicy,
    response: Response,
) -> None:
    decision = await container.rate_limiter.acquire(f"{name}:{identity}", policy)
    headers = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
    }
    if not decision.allowed:
        headers["Retry-After"] = str(decision.retry_after_seconds)
        raise RateLimitedError("rate limit exceeded; retry later", headers=headers)
    response.headers.update(headers)


def limit_by_ip(name: PolicyName) -> Callable[..., Awaitable[None]]:
    """Limit anonymous traffic by client IP.

    Behind a load balancer, run uvicorn with ``--forwarded-allow-ips`` set to the
    proxy's address so ``request.client`` is the real client, not the proxy.
    """

    async def dependency(request: Request, response: Response, container: ContainerDep) -> None:
        if not container.settings.rate_limit_enabled:
            return
        client_ip = request.client.host if request.client else "unknown"
        await _enforce(container, name, f"ip:{client_ip}", _policy(container, name), response)

    return dependency


def limit_by_user(name: PolicyName) -> Callable[..., Awaitable[None]]:
    """Limit authenticated traffic per user, with larger buckets for admins."""

    async def dependency(
        response: Response, container: ContainerDep, principal: PrincipalDep
    ) -> None:
        if not container.settings.rate_limit_enabled:
            return
        policy = _policy(container, name)
        if principal.is_admin:
            policy = policy.scaled(container.settings.rate_limit_admin_multiplier)
        await _enforce(container, name, f"user:{principal.user_id}", policy, response)

    return dependency
