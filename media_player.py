from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature
from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .api import NovaCloudAPI

DOMAIN = "novacloud"

async def async_setup_entry(hass, entry, async_add_entities):
    creds = hass.data[DOMAIN]
    app_key = creds["app_key"]
    app_secret = creds["app_secret"]
    webhook_url = creds["webhook_url"]
    api = NovaCloudAPI(creds["app_key"], creds["app_secret"])
    players = await api.get_players()

    entities = []
    for player in players.get("rows", []):
        entity = NovaCloudMediaPlayerEntity(player, creds["app_key"], creds["app_secret"])
        await entity.async_update()
        entities.append(entity)
    async_add_entities(entities, True)


class NovaCloudMediaPlayerEntity(MediaPlayerEntity):
    _attr_should_poll = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, player_data, app_key, app_secret):
        self._player = player_data
        self._player_id = player_data["playerId"]
        self._app_key = app_key
        self._app_secret = app_secret
        self._attr_name = f"{player_data['name']} Player"
        self._attr_unique_id = f"{self._player_id}_media_player"
        self._attr_volume_level = 1.0
        self._attr_state = MediaPlayerState.ON
        self._source = "Internal"
        self._source_list = ["Internal", "External"]

    async def async_added_to_hass(self):
        async_dispatcher_connect(
            self.hass, f"{DOMAIN}_webhook_data", self._handle_webhook_data
        )

    async def _handle_webhook_data(self, data):
        for item in data:
            if item.get("playerId") != self._player_id:
                continue
            if item.get("command") == "volumeValue":
                try:
                    ratio = item.get("data", {}).get("ratio")
                    percent = float(ratio)
                    self._attr_volume_level = percent / 100.0
                except (TypeError, ValueError):
                    self._attr_volume_level = 1.0
            if item.get("command") == "videoSourceValue":
                source = item.get("data", {}).get("videoSource")
                self._source = "Internal" if source == 0 else "External"
        self.async_write_ha_state()

    @property
    def source(self):
        return self._source

    @property
    def source_list(self):
        return self._source_list

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

    async def async_set_volume_level(self, volume: float):
        percent = round(volume * 100)
        api = NovaCloudAPI(self._app_key, self._app_secret)
        await api.set_volume(self._player_id, percent)
        self._attr_volume_level = volume
        self.async_write_ha_state()

    async def async_select_source(self, source: str):
        api = NovaCloudAPI(self._app_key, self._app_secret)
        source_val = 0 if source == "Internal" else 1
        await api.set_video_source(self._player_id, source_val)
        self._source = source
        self.async_write_ha_state()

    async def async_update(self):
        pass  # No longer needed with push-based webhook updates
