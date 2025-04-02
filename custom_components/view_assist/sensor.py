"""VA Sensors."""

from collections.abc import Callable
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.config_validation import make_entity_service_schema
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    DOMAIN,
    VA_ATTRIBUTE_UPDATE_EVENT,
    VA_BACKGROUND_UPDATE_EVENT,
    VAConfigEntry,
)
from .helpers import get_device_id_from_entity_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: VAConfigEntry, async_add_entities
):
    """Set up sensors from a config entry."""
    sensors = [ViewAssistSensor(hass, config_entry)]
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        name="set_state",
        schema=make_entity_service_schema({str: cv.match_all}, extra=vol.ALLOW_EXTRA),
        func="set_entity_state",
    )

    async_add_entities(sensors)


class ViewAssistSensor(SensorEntity):
    """Representation of a View Assist Sensor."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise the sensor."""

        self.hass = hass
        self.config = config

        self._attr_name = config.runtime_data.name
        self._type = config.runtime_data.type
        self._attr_unique_id = f"{self._attr_name}_vasensor"
        self._attr_native_value = ""
        self._attribute_listeners: dict[str, Callable] = {}

        self._voice_device_id = get_device_id_from_entity_id(
            self.hass, self.config.runtime_data.mic_device
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity is about to be added to hass."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self.config.entry_id}_update",
                self.va_update,
            )
        )

        # Add listener to timer changes
        self.hass.data[DOMAIN]["timers"].store.add_listener(
            self.entity_id, self.va_update
        )

    @callback
    def va_update(self, *args):
        """Update entity."""
        _LOGGER.debug("Updating: %s", self.entity_id)
        self.schedule_update_ha_state(True)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity attributes."""
        r = self.config.runtime_data
        attrs = {
            "type": r.type,
            "mic_device": r.mic_device,
            "mic_device_id": get_device_id_from_entity_id(self.hass, r.mic_device),
            "mediaplayer_device": r.mediaplayer_device,
            "musicplayer_device": r.musicplayer_device,
            "mode": r.mode,
            "view_timeout": r.view_timeout,
            "do_not_disturb": r.do_not_disturb,
            "status_icons": r.status_icons,
            "status_icons_size": r.status_icons_size,
            "assist_prompt": r.assist_prompt,
            "font_style": r.font_style,
            "use_24_hour_time": r.use_24_hour_time,
            "use_announce": r.use_announce,
            "background": r.background,
            "weather_entity": r.weather_entity,
            "mic_type": r.mic_type,
            "voice_device_id": self._voice_device_id,
        }

        # Only add these attributes if they exist
        if r.display_device:
            attrs["display_device"] = r.display_device

        # Add extra_data attributes from runtime data
        attrs.update(self.config.runtime_data.extra_data)

        # display timers
        attrs["timers"] = self.hass.data[DOMAIN]["timers"].get_timers(
            device_or_entity_id=self.entity_id, include_expired=True
        )

        return attrs

    def set_entity_state(self, **kwargs):
        """Set the state of the entity."""
        for k, v in kwargs.items():
            if k == "entity_id":
                continue
            if k == "allow_create":
                continue
            if k == "state":
                self._attr_native_value = v
                continue

            # Fire event if value changes to entity listener
            if hasattr(self.config.runtime_data, k):
                old_val = getattr(self.config.runtime_data, k)
            elif self.config.runtime_data.extra_data.get(k) is not None:
                old_val = self.config.runtime_data.extra_data[k]
            else:
                old_val = None
            if v != old_val:
                kwargs = {"attribute": k, "old_value": old_val, "new_value": v}
                self.hass.bus.fire(
                    VA_ATTRIBUTE_UPDATE_EVENT.format(self.config.entry_id), kwargs
                )

                # Fire background changed event to support linking device backgrounds
                if k == "background":
                    self.hass.bus.fire(
                        VA_BACKGROUND_UPDATE_EVENT.format(self.entity_id), kwargs
                    )

            # Set the value of named vartiables or add/update to extra_data dict
            if hasattr(self.config.runtime_data, k):
                setattr(self.config.runtime_data, k, v)
            else:
                self.config.runtime_data.extra_data[k] = v

        self.schedule_update_ha_state(True)

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return "mdi:glasses"
