from homeassistant import config_entries
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResult

DOMAIN = "novacloud"

class NovaCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="NovaCloud", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("app_key"): str,
                vol.Required("app_secret"): str,
                vol.Optional("scan_interval", default=300): int
            })
        )
