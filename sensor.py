from datetime import timedelta
from homeassistant.helpers.entity import Entity
from homeassistant.const import STATE_UNKNOWN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api import NovaCloudAPI

DOMAIN = "novacloud"

async def async_setup_entry(hass, entry, async_add_entities):
    creds = hass.data[DOMAIN]
    api = NovaCloudAPI(creds["app_key"], creds["app_secret"])
    players = await api.get_players()

    entities = []
    for player in players.get("rows", []):
        entities.append(NovaCloudPlayerSensor(player, entry.entry_id, creds["scan_interval"]))
    async_add_entities(entities)


class NovaCloudPlayerSensor(Entity):
    def __init__(self, player_data, config_entry_id, scan_interval):
        self._player = player_data
        self._player_id = player_data["playerId"]
        self._app_key = None
        self._app_secret = None
        self._scan_interval = timedelta(seconds=scan_interval)
        self._attr_name = f"{player_data['name']} Status"
        self._attr_unique_id = f"{self._player_id}_status"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._player_id)},
            "name": self._player["name"],
            "manufacturer": "NovaStar",
            "model": self._player.get("productName", "Unknown"),
            "sw_version": self._player.get("version", "Unknown"),
            "configuration_url": "https://us.vnnox.com/"
        }

    @property
    def should_poll(self):
        return True

    @property
    def scan_interval(self):
        return self._scan_interval

    async def async_update(self):
        if not self._app_key or not self._app_secret:
            creds = self.hass.data.get(DOMAIN, {})
            self._app_key = creds.get("app_key")
            self._app_secret = creds.get("app_secret")

        api = NovaCloudAPI(self._app_key, self._app_secret)
        result = await api.get_players()
        for player in result.get("rows", []):
            if player["playerId"] == self._player_id:
                self._player = player
                break

    @property
    def state(self):
        return "online" if self._player.get("onlineStatus") == 1 else "offline"

    @property
    def extra_state_attributes(self):
        return {
            "ip": self._player.get("ip"),
            "sn": self._player.get("sn"),
            "product_name": self._player.get("productName"),
            "resolution": f"{self._player.get('width')}x{self._player.get('height')}",
            "os_version": self._player.get("osVersion"),
            "last_online": self._player.get("lastOnlineTime"),
            "register_time": self._player.get("registerTime"),
        }
