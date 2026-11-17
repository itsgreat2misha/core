"""The Bravia TV integration."""

import socket
from typing import Final
from urllib.parse import urlparse

from aiohttp import CookieJar
from pybravia import BraviaClient

from homeassistant.components import ssdp
from homeassistant.const import CONF_HOST, CONF_MAC, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.util.network import is_ip_address

from .const import CONF_USE_SSL, DOMAIN
from .coordinator import BraviaTVConfigEntry, BraviaTVCoordinator

PLATFORMS: Final[list[Platform]] = [
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
]


async def async_setup_entry(
    hass: HomeAssistant, config_entry: BraviaTVConfigEntry
) -> None:
    """Set up a config entry."""
    host = config_entry.data[CONF_HOST]
    mac = config_entry.data[CONF_MAC]
    ssl = config_entry.data.get(CONF_USE_SSL, False)

    session = async_create_clientsession(
        hass, cookie_jar=CookieJar(unsafe=True, quote_cookie=False)
    )
    client = BraviaClient(host, mac, session=session, ssl=ssl)
    coordinator = BraviaTVCoordinator(
        hass=hass,
        config_entry=config_entry,
        client=client,
    )

    await coordinator.async_config_entry_first_refresh()

    config_entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    async def async_ssdp_callback(
        discovery_info: SsdpServiceInfo, change: ssdp.SsdpChange
    ) -> None:
        # The SSDP _host header is always an IP address, so it cannot be
        # used to filter in async_register_callback when the configured
        # host is a hostname. Filter here instead, using the host from
        # the announcement location, which a relay forwards unchanged.
        announced_host = None
        if discovery_info.ssdp_location:
            try:
                announced_host = urlparse(discovery_info.ssdp_location).hostname
            except ValueError:
                return
        if not announced_host:
            announced_host = discovery_info.ssdp_headers.get("_host")
        if not announced_host:
            return
        if announced_host.lower() != host.lower():
            if is_ip_address(host):
                return
            try:
                resolved_host = await hass.async_add_executor_job(
                    socket.gethostbyname, host
                )
            except OSError:
                return
            if announced_host != resolved_host:
                return
        await coordinator.async_request_refresh()

    config_entry.async_on_unload(
        await ssdp.async_register_callback(
            hass,
            async_ssdp_callback,
            {"nt": "urn:schemas-upnp-org:device:MediaRenderer:1"},
        )
    )


async def async_unload_entry(
    hass: HomeAssistant, config_entry: BraviaTVConfigEntry
) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(config_entry, PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: BraviaTVConfigEntry
) -> None:
    """Migrate an old config entry."""
    if config_entry.version == 1 and config_entry.minor_version == 1:
        # Old entries from a TV without CID have an empty unique ID
        new_unique_id = config_entry.unique_id or dr.format_mac(
            config_entry.data[CONF_MAC]
        )

        if new_unique_id != config_entry.unique_id:
            device_registry = dr.async_get(hass)
            if device_entry := device_registry.async_get_device_by_identifier(
                (DOMAIN, ""), config_entry.entry_id
            ):
                new_identifiers = device_entry.identifiers.copy()
                new_identifiers.discard((DOMAIN, ""))
                new_identifiers.add((DOMAIN, new_unique_id))
                device_registry.async_update_device(
                    device_entry.id, new_identifiers=new_identifiers
                )

            @callback
            def update_unique_id(entity_entry: er.RegistryEntry) -> dict[str, str]:
                """Prefix the entity unique ID, which was the suffix alone."""
                return {"new_unique_id": f"{new_unique_id}{entity_entry.unique_id}"}

            await er.async_migrate_entries(
                hass, config_entry.entry_id, update_unique_id
            )

        hass.config_entries.async_update_entry(
            config_entry, unique_id=new_unique_id, minor_version=2
        )
