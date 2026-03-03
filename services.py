from .api import NovaCloudAPI
import logging
from homeassistant.helpers.device_registry import async_get as async_get_device_registry

_LOGGER = logging.getLogger(__name__)

async def async_setup_services(hass, config):
    async def handle_set_power(call):
        player_id = call.data["player_id"]
        power_on = call.data["power_on"]
        creds = hass.data["novacloud"]
        api = NovaCloudAPI(creds["app_key"], creds["app_secret"])
        await api.set_power(player_id, power_on)

    async def handle_discover_players(call):
        creds = hass.data["novacloud"]
        api = NovaCloudAPI(creds["app_key"], creds["app_secret"])
        players = await api.get_players()

        device_registry = async_get_device_registry(hass)
        for player in players.get("rows", []):
            device_registry.async_get_or_create(
                config_entry_id=call.context.id,
                identifiers={("novacloud", player["playerId"])},
                manufacturer="NovaStar",
                name=player["name"],
                model=player.get("version", "Unknown"),
                sw_version=player.get("version"),
                configuration_url="https://us.vnnox.com/"
            )
            _LOGGER.info(f"Registered NovaCloud device: {player['name']}")

    hass.services.async_register("novacloud", "set_power", handle_set_power)
    hass.services.async_register("novacloud", "discover_players", handle_discover_players)
