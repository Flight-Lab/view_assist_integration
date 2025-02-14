"""Integration services."""

from asyncio import TimerHandle
from datetime import datetime
import logging
import os
import random

import requests
import voluptuous as vol

from homeassistant.const import (
    CONF_DEVICE,
    CONF_DEVICE_ID,
    CONF_NAME,
    CONF_PATH,
    CONF_TYPE,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.helpers.event import partial

from .const import (
    CONF_DISPLAY_DEVICE,
    CONF_DISPLAY_TYPE,
    CONF_TIME,
    CONF_TIMER_ID,
    DOMAIN,
    VAConfigEntry,
)
from .timers import VATimers, decode_time_sentence

_LOGGER = logging.getLogger(__name__)

NAVIGATE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Required(CONF_PATH): str,
        vol.Required(CONF_DISPLAY_TYPE): str,
        vol.Optional(CONF_DISPLAY_DEVICE): str,
    }
)

SET_TIMER_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_TYPE): str,
        vol.Optional(CONF_NAME): str,
        vol.Required(CONF_TIME): str,
    }
)

CANCEL_TIMER_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TIMER_ID): str,
    }
)

GET_TIMERS_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TIMER_ID): str,
        vol.Optional(CONF_DEVICE_ID): str,
    }
)


class VAServices:
    """Class to manage services."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config

        self.navigate_task: dict[str, TimerHandle] = {}

    async def async_setup_services(self):
        """Initialise VA services."""

        self.hass.services.async_register(
            DOMAIN,
            "get_target_satellite",
            self.async_handle_get_target_satellite,
            supports_response=SupportsResponse.ONLY,
        )

        self.hass.services.async_register(
            DOMAIN,
            "navigate",
            self.async_handle_navigate,
            schema=NAVIGATE_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "set_timer",
            self.async_handle_set_timer,
            schema=SET_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

        self.hass.services.async_register(
            DOMAIN,
            "cancel_timer",
            self.async_handle_cancel_timer,
            schema=CANCEL_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

        self.hass.services.async_register(
            DOMAIN,
            "get_timers",
            self.async_handle_get_timers,
            schema=GET_TIMERS_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

        self.hass.services.async_register(
            DOMAIN,
            "get_random_image",
            self.async_handle_get_random_image,
            supports_response=SupportsResponse.ONLY,
        )

    # -----------------------------------------------------------------------
    # Get Target Satellite
    # Used to determine which VA satellite is being used based on its microphone device
    #
    # Sample usage
    # action: view_assist.get_target_satellite
    # data:
    #   device_id: 4385828338e48103f63c9f91756321df
    # -----------------------------------------------------------------------

    async def async_handle_get_target_satellite(
        self, call: ServiceCall
    ) -> ServiceResponse:
        """Handle a get target satellite lookup call."""
        device_id = call.data.get(CONF_DEVICE_ID)
        entity_registry = er.async_get(self.hass)

        entities = []

        entry_ids = [
            entry.entry_id for entry in self.hass.config_entries.async_entries(DOMAIN)
        ]

        for entry_id in entry_ids:
            integration_entities = er.async_entries_for_config_entry(
                entity_registry, entry_id
            )
            entity_ids = [entity.entity_id for entity in integration_entities]
            entities.extend(entity_ids)

        # Fetch the 'mic_device' attribute for each entity
        # compare the device_id of mic_device to the value passed in to the service
        # return the match for the satellite that contains that mic_device
        target_satellite_devices = []
        for entity_id in entities:
            if state := self.hass.states.get(entity_id):
                if mic_entity_id := state.attributes.get("mic_device"):
                    if mic_entity := entity_registry.async_get(mic_entity_id):
                        if mic_entity.device_id == device_id:
                            target_satellite_devices.append(entity_id)

        # Return the list of target_satellite_devices
        # This should match only one VA device
        return {"target_satellite": target_satellite_devices}

    # -----------------------------------------------------------------------
    # Handle Navigation
    # Used to determine how to change the view on the VA device
    #
    # action: view_assist.navigate
    # data:
    #   target_display_device: sensor.viewassist_office_browser_path
    #   target_display_type: browsermod
    #   path: /dashboard-viewassist/weather
    # ------------------------------------------------------------------------
    async def async_handle_navigate(self, call: ServiceCall):
        """Handle a navigate to view call."""

        va_entity_id = call.data.get(CONF_DEVICE)
        path = call.data.get(CONF_PATH)
        display_type = call.data.get(CONF_DISPLAY_TYPE)
        display_device = call.data.get(CONF_DISPLAY_DEVICE)

        # get config entry from entity id to allow access to browser_id parameter
        entity_registry = er.async_get(self.hass)
        if entity := entity_registry.async_get(va_entity_id):
            entity_config_entry: VAConfigEntry = (
                self.hass.config_entries.async_get_entry(entity.config_entry_id)
            )
            browser_id = entity_config_entry.runtime_data.browser_id

            if browser_id:
                # Cancel any previous call later (revert display) task if new navigate request comes in
                # for that browser id
                if self.navigate_task and self.navigate_task.get(browser_id):
                    if not self.navigate_task[browser_id].cancelled():
                        self.navigate_task[browser_id].cancel()
                    del self.navigate_task[browser_id]

                # TODO: Remove fixed revert path and make dynamic based on logic/settings/mode
                await self.async_browser_navigate(
                    browser_id=browser_id,
                    path=path,
                    display_device=display_device,
                    display_type=display_type,
                    revert_path="/view-assist/clock",
                    timeout=self.config.runtime_data.view_timeout,
                )

    async def async_browser_navigate(
        self,
        browser_id: str,
        path: str,
        display_device: str,
        display_type: str,
        revert_path: str | None = None,
        timeout: int = 10,
    ):
        """Navigate browser to defined view.

        Optionally revert to another view after timeout.
        """
        entity_registry = er.async_get(self.hass)
        display_entity = entity_registry.async_get(display_device)
        # display_domain = display_entity.domain
        # display_device_id = display_entity.device_id
        _LOGGER.info(
            "Navigating: browser_id: %s, path: %s, display_device: %s, display_entity: %s",
            browser_id,
            path,
            display_device,
            display_entity,
        )

        if display_type == "BrowserMod":
            await self.hass.services.async_call(
                "browser_mod",
                "navigate",
                {"browser_id": browser_id, "path": path},
            )
        elif display_type == "Remote Assist Display":
            await self.hass.services.async_call(
                "remote_assist_display",
                "navigate",
                {"target": browser_id, "path": path},
            )

        if revert_path and timeout:
            _LOGGER.info("Adding revert to %s in %ss", revert_path, timeout)
            self.navigate_task[browser_id] = self.hass.loop.call_later(
                timeout,
                partial(
                    self.hass.create_task,
                    self.async_browser_navigate(
                        browser_id, revert_path, display_device, display_type
                    ),
                    f"Revert browser {browser_id}",
                ),
            )

    # ----------------------------------------------------------------
    # TIMERS
    # ----------------------------------------------------------------
    async def async_handle_set_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        device_id = call.data.get(CONF_DEVICE_ID)
        timer_type = call.data.get(CONF_TYPE)
        name = call.data.get(CONF_NAME)
        timer_time = call.data.get(CONF_TIME)

        sentence, timer_info = decode_time_sentence(timer_time)
        if timer_info:
            t: VATimers = self.config.runtime_data._timers  # noqa: SLF001
            timer_id, timer, response = await t.add_timer(
                timer_type,
                device_id,
                timer_info,
                name,
                extra_info={"sentence": sentence},
            )

            return {"timer_id": timer_id, "timer": timer, "response": response}
        return {"error": "unable to decode time or interval information"}

    async def async_handle_cancel_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        if timer_id := call.data.get(CONF_TIMER_ID):
            t: VATimers = self.config.runtime_data._timers  # noqa: SLF001
            result = await t.cancel_timer(timer_id)
            return {"result": result}
        return {"error": "no timer id supplied"}

    async def async_handle_get_timers(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        device_id = call.data.get(CONF_DEVICE_ID)
        timer_id = call.data.get(CONF_TIMER_ID)

        t: VATimers = self.config.runtime_data._timers  # noqa: SLF001
        result = await t.get_timers(timer_id, device_id)
        return {"result": result}

    # ----------------------------------------------------------------
    # Images
    # ----------------------------------------------------------------
    async def async_handle_get_random_image(self, call: ServiceCall) -> ServiceResponse:
        """Handle random image selection.

        name: View Assist Select Random Image
        description: Selects a random image from the specified directory or downloads a new image
        """
        directory = call.data.get("directory")
        source = call.data.get(
            "source", "local"
        )  # Default to "local" if source is not provided

        valid_extensions = (".jpeg", ".jpg", ".tif", ".png")

        if source == "local":
            # Translate /local/ to /config/www/ for directory validation
            if directory.startswith("/local/"):
                filesystem_directory = directory.replace("/local/", "/config/www/", 1)
            else:
                filesystem_directory = directory

            # Verify the directory exists
            if not os.path.isdir(filesystem_directory):
                return {
                    "error": f"The directory '{filesystem_directory}' does not exist."
                }

            # List only image files with the valid extensions
            images = [
                f
                for f in os.listdir(filesystem_directory)
                if f.lower().endswith(valid_extensions)
            ]

            # Check if any images were found
            if not images:
                return {
                    "error": f"No images found in the directory '{filesystem_directory}'."
                }

            # Select a random image
            selected_image = random.choice(images)

            # Replace /config/www/ with /local/ for constructing the relative path
            if filesystem_directory.startswith("/config/www/"):
                relative_path = filesystem_directory.replace("/config/www/", "/local/")
            else:
                relative_path = directory

            # Ensure trailing slash in the relative path
            if not relative_path.endswith("/"):
                relative_path += "/"

            # Construct the image path
            image_path = f"{relative_path}{selected_image}"

        elif source == "download":
            url = "https://unsplash.it/640/425?random"
            response = requests.get(url)

            if response.status_code == 200:
                current_time = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = f"random_{current_time}.jpg"
                full_path = os.path.join(directory, filename)

                with open(full_path, "wb") as file:
                    file.write(response.content)

                # Remove previous background image
                for file in os.listdir(directory):
                    if file.startswith("random_") and file != filename:
                        os.remove(os.path.join(directory, file))

                image_path = full_path
            else:
                # Return existing image if the download fails
                existing_files = [
                    os.path.join(directory, file)
                    for file in os.listdir(directory)
                    if file.startswith("random_")
                ]
                image_path = existing_files[0] if existing_files else None

            if not image_path:
                return {
                    "error": "Failed to download a new image and no existing images found."
                }

        else:
            return {"error": "Invalid source specified. Use 'local' or 'download'."}

        # Return the image path in a dictionary
        return {"image_path": image_path}
