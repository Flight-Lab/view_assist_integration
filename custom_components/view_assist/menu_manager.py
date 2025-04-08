"""Menu manager for View Assist."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant, State

from .const import (
    CONF_ENABLE_MENU,
    CONF_ENABLE_MENU_TIMEOUT,
    CONF_MENU_AUTO_CLOSE,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    DEFAULT_ENABLE_MENU_TIMEOUT,
    DEFAULT_MENU_AUTO_CLOSE,
    DEFAULT_MENU_ITEMS,
    DEFAULT_MENU_TIMEOUT,
    DOMAIN,
    VAConfigEntry,
)
from .helpers import get_config_entry_by_entity_id, get_sensor_entity_from_instance

_LOGGER = logging.getLogger(__name__)


class MenuManager:
    """Class to manage View Assist menus."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize menu manager."""
        self.hass = hass
        self.config = config
        self._active_menus: dict[str, bool] = {}  # Track open menus by entity_id
        self._timeouts: dict[str, asyncio.Task] = {}  # Track menu timeout timers
        
        # Register for state changes to handle initialization
        self.hass.bus.async_listen_once("homeassistant_started", self._on_ha_started)

    async def _on_ha_started(self, event):
        """Initialize menu states after Home Assistant has started."""
        # Check all VA entities to ensure they don't have current view in menu
        for entry_id in [entry.entry_id for entry in self.hass.config_entries.async_entries(DOMAIN)]:
            entity_id = get_sensor_entity_from_instance(self.hass, entry_id)
            if entity_id:
                # Perform initial filtering of menu icons based on current view
                await self.refresh_menu(entity_id)

    async def toggle_menu(self, entity_id: str, show: bool = None, menu_items: list[str] = None, timeout: int = None) -> None:
        """Toggle menu visibility for an entity."""
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            _LOGGER.warning("No config entry found for entity %s", entity_id)
            return

        # Check if menu is enabled
        if not config_entry.options.get(CONF_ENABLE_MENU, False):
            _LOGGER.debug("Menu is not enabled for %s", entity_id)
            return

        # Get current state
        current_state = self.hass.states.get(entity_id)
        if not current_state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return
            
        # If show not specified, toggle based on current state
        current_active = current_state.attributes.get("menu_active", False)
        if show is None:
            show = not current_active
            
        _LOGGER.debug("Menu toggle for %s: current=%s, new=%s", entity_id, current_active, show)
        
        # Cancel any existing timeout
        self._cancel_timeout(entity_id)

        # Update menu state
        self._active_menus[entity_id] = show

        # Get current status icons and available menu items
        current_icons = current_state.attributes.get("status_icons", [])
        config_items = config_entry.options.get(CONF_MENU_ITEMS, DEFAULT_MENU_ITEMS)
        items_to_use = menu_items or config_items

        if show:
            # Get current view for filtering
            current_view = self._get_current_view(current_state)
            _LOGGER.debug("Current view for filtering: %s", current_view)
            
            # First remove any existing menu items
            base_icons = [icon for icon in current_icons if icon not in config_items]
            
            # Now add all menu items that aren't the current view
            menu_icons = []
            for item in items_to_use:
                # Skip if it's the current view
                if current_view and item == current_view:
                    continue
                menu_icons.append(item)
                
            # Combine base icons with filtered menu icons
            updated_icons = menu_icons + base_icons
            
            # Update entity with new status icons
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": updated_icons,
                    "menu_active": True,  # Explicitly set to True
                },
            )
            
            # Set up timeout if enabled or specified
            if timeout is not None:
                self._setup_timeout(entity_id, timeout)
            elif config_entry.options.get(CONF_ENABLE_MENU_TIMEOUT, DEFAULT_ENABLE_MENU_TIMEOUT):
                timeout_value = config_entry.options.get(CONF_MENU_TIMEOUT, DEFAULT_MENU_TIMEOUT)
                self._setup_timeout(entity_id, timeout_value)
        else:
            # When hiding, remove all menu items
            updated_icons = [icon for icon in current_icons if icon not in config_items]
            
            # Update entity with filtered status icons
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": updated_icons,
                    "menu_active": False,  # Explicitly set to False
                },
            )

    async def refresh_menu(self, entity_id: str) -> None:
        """Refresh menu to ensure current view is filtered out."""
        current_state = self.hass.states.get(entity_id)
        if not current_state:
            return
            
        # Only refresh if menu is active
        if not current_state.attributes.get("menu_active", False):
            return
            
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            return
            
        # Re-apply menu toggle with current items
        await self.toggle_menu(entity_id, True)

    async def process_menu_action(self, entity_id: str, action: str) -> None:
        """Process a menu icon action (view, service, entity)."""
        # First check if auto-close is enabled before processing the action
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        auto_close = config_entry and config_entry.options.get(CONF_MENU_AUTO_CLOSE, DEFAULT_MENU_AUTO_CLOSE)
        
        # Handle different action types based on prefix
        # Format: "type:action"
        path_to_navigate = None
        
        if ":" in action:
            action_type, action_value = action.split(":", 1)
            
            if action_type == "view":
                # Navigate to a view
                path_to_navigate = f"/view-assist/{action_value}"
            elif action_type == "service":
                # Call a service
                domain, service = action_value.split(".", 1)
                await self.hass.services.async_call(
                    domain,
                    service,
                    {},
                )
            elif action_type == "entity":
                # Toggle an entity
                await self.hass.services.async_call(
                    "homeassistant",
                    "toggle",
                    {"entity_id": action_value},
                )
        else:
            # Default action - treat as view navigation
            path_to_navigate = f"/view-assist/{action}"

        # Close menu first if auto-close is enabled
        if auto_close:
            _LOGGER.debug("Auto-closing menu for %s after action", entity_id)
            await self.toggle_menu(entity_id, False)
        
        # Then navigate if needed (after menu is closed)
        if path_to_navigate:
            await self.hass.services.async_call(
                DOMAIN,
                "navigate",
                {
                    "device": entity_id,
                    "path": path_to_navigate,
                },
            )

    def _get_current_view(self, state: State) -> str | None:
        """Get the current view from a state object."""
        # First check for current_path attribute
        if current_path := state.attributes.get("current_path"):
            match = re.search(r"/view-assist/([^/]+)", current_path)
            if match:
                return match.group(1)
        
        # Try to get from display device
        try:
            display_device = state.attributes.get("display_device")
            if not display_device:
                return None
                
            # Find browser or path entities for this device
            for entity in self.hass.states.async_all():
                if entity.attributes.get("device_id") == display_device:
                    if "path" in entity.entity_id or "browser" in entity.entity_id:
                        # Check pathSegments attribute
                        if path_segments := entity.attributes.get("pathSegments"):
                            if len(path_segments) > 2 and path_segments[1] == "view-assist":
                                return path_segments[2]
                                
                        # Try entity state or path attribute
                        if path := entity.state:
                            match = re.search(r"/view-assist/([^/]+)", path)
                            if match:
                                return match.group(1)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("Error determining current view: %s", ex)
        
        return None

    def _setup_timeout(self, entity_id: str, timeout: int) -> None:
        """Setup timeout for menu."""
        self._timeouts[entity_id] = self.hass.async_create_task(
            self._timeout_task(entity_id, timeout)
        )
    
    async def _timeout_task(self, entity_id: str, timeout: int) -> None:
        """Task to handle menu timeout."""
        try:
            await asyncio.sleep(timeout)
            _LOGGER.debug("Menu timeout triggered for %s", entity_id)
            await self.toggle_menu(entity_id, False)
        except asyncio.CancelledError:
            # Normal when timeout is cancelled
            pass
        
    def _cancel_timeout(self, entity_id: str) -> None:
        """Cancel any existing timeout for an entity."""
        if task := self._timeouts.pop(entity_id, None):
            if not task.done():
                task.cancel()
