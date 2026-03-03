import aiohttp
import time
import secrets
import hashlib
import json
import logging

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://open-us.vnnox.com"

def screen_power_url():
    return f"{API_BASE}/v2/player/power/onOrOff"

def screen_status_url():
    return f"{API_BASE}/v2/player/real-time-control/screen-status"

def player_list_url():
    return f"{API_BASE}/v2/player/list"

def brightness_set_url():
    return f"{API_BASE}/v2/player/real-time-control/brightness"

def volume_set_url():
    return f"{API_BASE}/v2/player/real-time-control/volume"

def running_status_url():
    return f"{API_BASE}/v2/player/current/running-status"

def video_source_url():
    return f"{API_BASE}/v2/player/real-time-control/video-source"

def generate_checksum(app_secret, nonce, cur_time):
    input_str = app_secret + nonce + cur_time
    return hashlib.sha256(input_str.encode("utf-8")).hexdigest()

class NovaCloudAPI:
    def __init__(self, app_key, app_secret, webhook_url=None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.webhook_url = webhook_url  # <-- store the webhook url

    def _auth_headers(self):
        nonce = secrets.token_hex(8)
        cur_time = str(int(time.time()))
        checksum = generate_checksum(self.app_secret, nonce, cur_time)
        return {
            "AppKey": self.app_key,
            "Nonce": nonce,
            "CurTime": cur_time,
            "CheckSum": checksum,
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Encoding": "identity"
        }

    async def get_players(self, count=100, start=0):
        url = player_list_url()
        headers = self._auth_headers()
        params = {"count": count, "start": start}
        async with aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    _LOGGER.error(f"Fetching player list failed: {resp.status} - {text}")
                    return {}

    async def set_screen_status(self, player_id, status="OPEN"):
        url = screen_status_url()
        headers = self._auth_headers()
        payload = {
            "playerIds": [player_id],
            "status": status
        }
        async with aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error(f"Screen status change failed: {resp.status} - {text}")

    async def set_brightness(self, player_id, value: int):
        url = brightness_set_url()
        headers = self._auth_headers()
        payload = {
            "playerIds": [player_id],
            "value": value
        }
        async with aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error(f"Set brightness failed: {resp.status} - {text}")

    async def set_volume(self, player_id: str, value: int):
        url = volume_set_url()
        headers = self._auth_headers()
        payload = {
            "playerIds": [player_id],
            "value": value
        }
        async with aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error(f"Volume set failed: {resp.status} - {text}")

    async def set_video_source(self, player_id: str, source: int):
        url = video_source_url()
        headers = self._auth_headers()
        payload = {
            "playerIds": [player_id],
            "source": source
        }
        async with aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error(f"Video source switch failed: {resp.status} - {text}")

    async def get_status_data(self, player_id: str, commands: list):
        url = running_status_url()
        headers = self._auth_headers()
        payload = {
            "playerIds": [player_id],
            "commands": commands,
            "noticeUrl": self.webhook_url
        }

        async with aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                _LOGGER.debug("Sent async status fetch with payload: %s", payload)
                _LOGGER.debug("Status code: %s", resp.status)
                return {}  # API won't respond here, so return empty
