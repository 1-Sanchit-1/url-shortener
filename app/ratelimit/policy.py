from pydantic import BaseModel


class RateLimitPolicy(BaseModel):
    """A token bucket: bursts of up to ``capacity``, sustained ``refill_per_second``."""

    capacity: int
    refill_per_second: float

    def scaled(self, factor: float) -> "RateLimitPolicy":
        return RateLimitPolicy(
            capacity=max(1, round(self.capacity * factor)),
            refill_per_second=self.refill_per_second * factor,
        )
