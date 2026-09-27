from cua.enums import Driver


class LeaseError(Exception):
    pass


class ControlLease:
    """Whoever holds the lease may touch the live session. Epoch invalidates stale holders."""

    def __init__(self) -> None:
        """Start with no driver holding the live session."""
        self.holder: Driver = Driver.NONE
        self.epoch: int = 0

    def grant(self, driver: Driver) -> int:
        """Give the live session to automation or a human; bump the epoch."""
        self.holder = driver
        self.epoch += 1
        return self.epoch

    def release(self) -> None:
        """Clear the holder so nobody owns the session."""
        self.holder = Driver.NONE

    def assert_held_by(self, driver: Driver, epoch: int) -> None:
        """Raise if this driver/epoch no longer owns the session."""
        if self.holder != driver or self.epoch != epoch:
            raise LeaseError(
                f"lease not held by {driver.value}@{epoch} "
                f"(holder={self.holder.value} epoch={self.epoch})"
            )
