"""View Assist custom integration."""

import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .alarm_repeater import ALARMS, VAAlarmRepeater
from .const import DOMAIN, RuntimeData, VAConfigEntry
from .dashboard import DASHBOARD_MANAGER, DashboardManager
from .entity_listeners import EntityListeners
from .helpers import (
    ensure_list,
    get_device_name_from_id,
    get_loaded_instance_count,
    is_first_instance,
)
from .http import HTTPManager
from .js_modules import JSModuleRegistration
from .services import VAServices
from .templates import setup_va_templates
from .timers import TIMERS, VATimers
from .websocket import async_register_websockets

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: VAConfigEntry):
    """Set up View Assist from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Add runtime data to config entry to have place to store data and
    # make accessible throughout integration
    entry.runtime_data = RuntimeData()
    set_runtime_data_from_config(entry)

    # Add config change listener
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Run first instance only functions
    if is_first_instance(hass, entry, display_instance_only=False):
        await run_if_first_instance(hass, entry)

    # Run first display instance only functions
    if is_first_instance(hass, entry, display_instance_only=True):
        await run_if_first_display_instance(hass, entry)

    # Load entity listeners
    EntityListeners(hass, entry)

    # Load websockets
    await async_register_websockets(hass)

    # Request platform setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Fire display device registration to setup display if first time config
    async_dispatcher_send(
        hass,
        f"{DOMAIN}_{get_device_name_from_id(hass, entry.runtime_data.display_device)}_registered",
    )

    return True


async def run_if_first_instance(hass: HomeAssistant, entry: VAConfigEntry):
    """Things to run only for first instance of integration."""

    # Inisitialise service
    services = VAServices(hass, entry)
    await services.async_setup_services()

    # Setup Timers
    timers = VATimers(hass, entry)
    hass.data[DOMAIN][TIMERS] = timers
    await timers.load()

    # Load javascript modules
    jsloader = JSModuleRegistration(hass)
    await jsloader.async_register()

    hass.data[DOMAIN][ALARMS] = VAAlarmRepeater(hass, entry)

    setup_va_templates(hass)


async def run_if_first_display_instance(hass: HomeAssistant, entry: VAConfigEntry):
    """Things to run only one when multiple instances exist."""

    # Run dashboard and view setup
    async def setup_frontend(*args):
        http = HTTPManager(hass, entry)
        await http.create_url_paths()

        dm = DashboardManager(hass, entry)
        hass.data[DOMAIN][DASHBOARD_MANAGER] = dm
        await dm.setup_dashboard()

    if hass.is_running:
        await setup_frontend()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, setup_frontend)


def set_runtime_data_from_config(config_entry: VAConfigEntry):
    """Set config.runtime_data attributes from matching config values."""

    config_sources = [config_entry.data, config_entry.options]
    for source in config_sources:
        for k, v in source.items():
            if hasattr(config_entry.runtime_data, k):
                # This is a fix for config lists being a string
                if isinstance(getattr(config_entry.runtime_data, k), list):
                    setattr(config_entry.runtime_data, k, ensure_list(v))
                else:
                    setattr(config_entry.runtime_data, k, v)


async def _async_update_listener(hass: HomeAssistant, config_entry: VAConfigEntry):
    """Handle config options update."""
    # Reload the integration when the options change.
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VAConfigEntry):
    """Unload a config entry."""

    # Unload js resources
    if get_loaded_instance_count(hass) <= 1:
        # Unload lovelace module resource if only instance
        _LOGGER.debug("Removing javascript modules cards")
        jsloader = JSModuleRegistration(hass)
        await jsloader.async_unregister()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
