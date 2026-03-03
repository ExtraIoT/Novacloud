import logging
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.components.webhook import async_register, async_unregister
from homeassistant.helpers.network import get_url
from aiohttp import web

DOMAIN = "novacloud"
WEBHOOK_ID = f"{DOMAIN}_webhook"
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict):
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    webhook_url = f"{get_url(hass, allow_internal=False)}/api/webhook/{WEBHOOK_ID}"
    creds = {
        "app_key": entry.data["app_key"],
        "app_secret": entry.data["app_secret"],
        "scan_interval": entry.data.get("scan_interval", 300),
        "webhook_url": webhook_url
    }
    hass.data[DOMAIN] = creds

    # Register webhook
    async_register(hass, DOMAIN, WEBHOOK_ID, "NovaCloud Webhook", handle_webhook)
    _LOGGER.info("NovaCloud webhook registered at %s", webhook_url)

    await hass.config_entries.async_forward_entry_setups(entry, ["light", "sensor", "media_player"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    await async_unregister(hass, WEBHOOK_ID)
    return True

@callback
async def handle_webhook(hass: HomeAssistant, webhook_id: str, request):
    try:
        data = await request.json()
        _LOGGER.warning("=== NOVACLOUD WEBHOOK TRIGGERED ===")
        _LOGGER.debug("Webhook received: %s", data)
        async_dispatcher_send(hass, f"{DOMAIN}_webhook_data", data)
        return web.Response(text="ok")  # 👈 Return "ok" string
    except Exception as e:
        _LOGGER.error("Error handling NovaCloud webhook: %s", e)
        return web.Response(status=500, text="error")
