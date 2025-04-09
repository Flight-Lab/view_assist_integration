"""Menu manager for View Assist."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENABLE_MENU,
    CONF_ENABLE_MENU_TIMEOUT,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    DEFAULT_ENABLE_MENU_TIMEOUT,
    DEFAULT_MENU_ITEMS,
    DEFAULT_MENU_TIMEOUT,
    DOMAIN,
    VAConfigEntry,
)
from .helpers import get_config_entry_by_entity_id

_LOGGER = logging.getLogger(__name__)


class MenuManager:
    """Class to manage View Assist menus."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize menu manager."""
        self.hass = hass
        self.config = config
        self._active_menus: dict[str, bool] = {}  # Track open menus by entity_id
        self._timeouts: dict[str, asyncio.Task] = {}  # Track menu timeout timers

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

        # Get available menu items
        config_items = config_entry.options.get(CONF_MENU_ITEMS, DEFAULT_MENU_ITEMS)
        items_to_use = menu_items or config_items

        if show:
            # Update entity with menu active state
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": items_to_use + (current_state.attributes.get("status_icons", []) or []),
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
            updated_icons = [icon for icon in current_state.attributes.get("status_icons", []) 
                            if icon not in config_items]
            
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
