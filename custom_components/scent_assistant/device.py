    async def _advance_oil_estimate(self) -> None:
        """Account for oil consumed up to now before mutating state."""
        if self.oil_estimator is not None:
            await self.oil_estimator.async_advance()

    def _schedule_oil_advance(self) -> None:
        """Schedule a single serialized oil estimate update from a BLE callback."""
        estimator = self.oil_estimator
        if estimator is None or not estimator.enabled:
            return

        pending = getattr(self, "_oil_advance_task", None)
        if pending is not None and not pending.done():
            return

        async def _run() -> None:
            try:
                await estimator.async_advance()
            except Exception:
                _LOGGER.exception("Error advancing estimated oil usage")

        if self._hass is not None:
            self._oil_advance_task = self._hass.async_create_task(
                _run(),
                f"{DOMAIN} oil estimate {self.unique_id}",
            )
        else:
            try:
                self._oil_advance_task = asyncio.get_running_loop().create_task(_run())
            except RuntimeError:
                pass
