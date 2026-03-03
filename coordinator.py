import logging
from datetime import timedelta

from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.core import HomeAssistant
from .api import NovaCloudAPI
DOMAIN = "novacloud"

_LOGGER = logging.getLogger(__name__)

class NovaCloudCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: NovaCloudAPI, scan_interval: int):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="NovaCloud Data Coordinator",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self.players = {}

    async def _async_update_data(self):
        """Fetch data from NovaCloud."""
        try:
            result = await self.api.get_players()
            player_rows = result.get("rows", [])
            self.players = {player["playerId"]: player for player in player_rows}
            return self.players
        except Exception as err:
            raise UpdateFailed(f"Error fetching NovaCloud data: {err}")
