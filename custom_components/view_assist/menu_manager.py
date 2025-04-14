"""Menu manager for View Assist."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Literal, Optional, Union

from homeassistant.core import HomeAssistant, State

from .const import (
    CONF_ENABLE_MENU,
    CONF_ENABLE_MENU_TIMEOUT,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    CONF_SHOW_MENU_BUTTON,
    DEFAULT_ENABLE_MENU,
    DEFAULT_ENABLE_MENU_TIMEOUT,
    DEFAULT_MENU_ITEMS,
    DEFAULT_MENU_TIMEOUT,
    DEFAULT_SHOW_MENU_BUTTON,
    DOMAIN,
)
from .helpers import (
    ensure_menu_button_at_end,
    get_config_entry_by_entity_id,
    get_sensor_entity_from_instance,
    normalize_status_items,
)

_LOGGER = logging.getLogger(__name__)

StatusItemType = Union[str, List[str]]
MenuTargetType = Literal["status_icons", "menu_items"]


@dataclass
class MenuState:
    """Structured representation of a menu's state."""
    entity_id: str
    active: bool = False
    configured_items: List[str] = field(default_factory=list)
    status_icons: List[str] = field(default_factory=list)
    system_icons: List[str] = field(default_factory=list)
    menu_timeout: Optional[asyncio.Task] = None
    item_timeouts: Dict[str, asyncio.Task] = field(default_factory=dict)


class MenuManager:
    """Class to manage View Assist menus."""

    def __init__(self, hass: HomeAssistant, config: Any) -> None:
        """Initialize menu manager."""
        self.hass = hass
        self.config = config
        self._menu_states: Dict[str, MenuState] = {}
        self._pending_updates: Dict[str, Dict[str, Any]] = {}
        self._update_task: Optional[asyncio.Task] = None

        self.hass.bus.async_listen_once(
            "homeassistant_started", self._on_ha_started)

    async def _on_ha_started(self, event) -> None:
        """Initialize menu states after Home Assistant has started."""
        for entry_id in [entry.entry_id for entry in self.hass.config_entries.async_entries(DOMAIN)]:
            entity_id = get_sensor_entity_from_instance(self.hass, entry_id)
            if entity_id:
                await self.refresh_menu(entity_id)

    def _get_or_create_state(self, entity_id: str) -> MenuState:
        """Get or create a MenuState for the entity."""
        state = self.hass.states.get(entity_id)
        if not state:
            if entity_id not in self._menu_states:
                self._menu_states[entity_id] = MenuState(entity_id=entity_id)
            return self._menu_states[entity_id]

        configured_items = state.attributes.get("menu_items", []) or []
        current_status_icons = state.attributes.get("status_icons", []) or []
        is_active = state.attributes.get("menu_active", False)
        system_icons = [icon for icon in current_status_icons
                        if icon not in set(configured_items) and icon != "menu"]

        if entity_id in self._menu_states:
            menu_state = self._menu_states[entity_id]
            menu_state.configured_items = configured_items
            menu_state.status_icons = current_status_icons
            menu_state.system_icons = system_icons
            menu_state.active = is_active
        else:
            self._menu_states[entity_id] = MenuState(
                entity_id=entity_id,
                active=is_active,
                configured_items=configured_items,
                status_icons=current_status_icons,
                system_icons=system_icons,
            )

        return self._menu_states[entity_id]

    async def toggle_menu(self, entity_id: str, show: Optional[bool] = None, timeout: Optional[int] = None) -> None:
        """Toggle menu visibility for an entity."""
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry or not config_entry.options.get(CONF_ENABLE_MENU, DEFAULT_ENABLE_MENU):
            _LOGGER.debug(
                "Menu not enabled or config not found for %s", entity_id)
            return

        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return

        menu_state = self._get_or_create_state(entity_id)

        current_active = menu_state.active
        if show is None:
            show = not current_active

        self._cancel_timeout(entity_id)

        show_menu_button = config_entry.options.get(
            CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)
        changes = {}

        if show:
            current_view = self._get_current_view(state)
            if current_view and current_view in menu_state.configured_items:
                menu_state.configured_items = [item for item in menu_state.configured_items
                                               if item != current_view]

            updated_icons = menu_state.status_icons.copy()

            for item in menu_state.configured_items:
                if item not in updated_icons:
                    updated_icons.append(item)

            if show_menu_button:
                ensure_menu_button_at_end(updated_icons)

            menu_state.active = True
            menu_state.status_icons = updated_icons

            changes = {
                "status_icons": updated_icons,
                "menu_active": True,
                "menu_items": menu_state.configured_items
            }

            if timeout is not None:
                self._setup_timeout(entity_id, timeout)
            elif config_entry.options.get(CONF_ENABLE_MENU_TIMEOUT, DEFAULT_ENABLE_MENU_TIMEOUT):
                timeout_value = config_entry.options.get(
                    CONF_MENU_TIMEOUT, DEFAULT_MENU_TIMEOUT)
                self._setup_timeout(entity_id, timeout_value)
        else:
            custom_icons = [icon for icon in menu_state.status_icons
                            if icon not in menu_state.configured_items and icon != "menu"]

            updated_icons = custom_icons.copy()
            for icon in menu_state.system_icons:
                if icon not in updated_icons:
                    updated_icons.append(icon)

            if show_menu_button and "menu" not in updated_icons:
                updated_icons.append("menu")

            menu_state.active = False
            menu_state.status_icons = updated_icons
            changes = {
                "status_icons": updated_icons,
                "menu_active": False
            }

        if changes:
            await self._update_entity_state(entity_id, changes)

    async def add_menu_item(self, entity_id: str, status_item: StatusItemType, menu: bool = False, timeout: Optional[int] = None) -> None:
        """Add status item(s) to the entity's status icons or menu items."""
        items = self._normalize_status_items(status_item)
        if not items:
            _LOGGER.warning("No valid items to add")
            return

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            _LOGGER.warning("No config entry found for entity %s", entity_id)
            return

        menu_state = self._get_or_create_state(entity_id)
        show_menu_button = config_entry.options.get(
            CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)
        changes = {}

        if menu:
            updated_items = menu_state.configured_items.copy()
            changed = False

            for item in items:
                if item not in updated_items:
                    updated_items.append(item)
                    changed = True

            if changed:
                menu_state.configured_items = updated_items
                changes["menu_items"] = updated_items

                if menu_state.active:
                    updated_icons = menu_state.status_icons.copy()

                    for item in items:
                        if item not in updated_icons:
                            updated_icons.append(item)

                    if show_menu_button:
                        ensure_menu_button_at_end(updated_icons)

                    menu_state.status_icons = updated_icons
                    changes["status_icons"] = updated_icons
        else:
            updated_icons = menu_state.status_icons.copy()
            changed = False

            for item in items:
                if item != "menu" and item not in updated_icons:
                    updated_icons.append(item)
                    changed = True

            if show_menu_button:
                ensure_menu_button_at_end(updated_icons)
                changed = True

            if changed:
                menu_state.status_icons = updated_icons
                changes["status_icons"] = updated_icons

        if changes:
            await self._update_entity_state(entity_id, changes)

        if timeout is not None:
            for item in items:
                await self._setup_item_timeout(entity_id, item, timeout, menu)

    async def remove_menu_item(self, entity_id: str, status_item: StatusItemType, menu: bool = False) -> None:
        """Remove status item(s) from the entity's status icons or menu items."""
        items = self._normalize_status_items(status_item)
        if not items:
            _LOGGER.warning("No valid items to remove")
            return

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            return

        # Get fresh state with current status icons
        menu_state = self._get_or_create_state(entity_id)
        show_menu_button = config_entry.options.get(
            CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)
        changes = {}

        if menu:
            updated_items = [
                item for item in menu_state.configured_items if item not in items]

            if updated_items != menu_state.configured_items:
                menu_state.configured_items = updated_items
                changes["menu_items"] = updated_items

                if menu_state.active:
                    updated_icons = menu_state.status_icons.copy()

                    for item in items:
                        if item in updated_icons and item in menu_state.configured_items:
                            updated_icons.remove(item)

                    if show_menu_button and "menu" not in updated_icons:
                        updated_icons.append("menu")

                    menu_state.status_icons = updated_icons
                    changes["status_icons"] = updated_icons
        else:
            updated_icons = menu_state.status_icons.copy()
            changed = False

            for item in items:
                if item == "menu" and show_menu_button:
                    continue

                if item in updated_icons:
                    updated_icons.remove(item)
                    changed = True
            if show_menu_button and "menu" not in updated_icons:
                updated_icons.append("menu")
                changed = True

            if changed:
                menu_state.status_icons = updated_icons
                changes["status_icons"] = updated_icons

        if changes:
            await self._update_entity_state(entity_id, changes)

        for item in items:
            self._cancel_item_timeout(entity_id, item, menu)

    async def refresh_menu(self, entity_id: str) -> None:
        """Refresh menu to ensure current view is filtered out and status is correct."""
        state = self.hass.states.get(entity_id)
        if not state:
            return

        menu_state = self._get_or_create_state(entity_id)

        if not menu_state.active:
            return

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            return

        non_menu_icons = [icon for icon in menu_state.status_icons
                          if icon not in menu_state.configured_items and icon != "menu"]

        current_view = self._get_current_view(state)
        if current_view and current_view in menu_state.configured_items:
            menu_state.configured_items = [item for item in menu_state.configured_items
                                           if item != current_view]

        show_menu_button = config_entry.options.get(
            CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)

        updated_icons = non_menu_icons.copy()

        for item in menu_state.configured_items:
            if item not in updated_icons:
                updated_icons.append(item)

        if show_menu_button:
            ensure_menu_button_at_end(updated_icons)

        changes = {
            "status_icons": updated_icons,
            "menu_active": True,
            "menu_items": menu_state.configured_items
        }

        await self._update_entity_state(entity_id, changes)

    def _get_current_view(self, state: State) -> Optional[str]:
        """Get the current view from a state object."""
        if current_path := state.attributes.get("current_path"):
            match = re.search(r"/view-assist/([^/]+)", current_path)
            if match:
                return match.group(1)

        display_device = state.attributes.get("display_device")
        if not display_device:
            return None

        for entity in self.hass.states.async_all():
            if entity.attributes.get("device_id") == display_device and (
                    "path" in entity.entity_id or "browser" in entity.entity_id):
                path = entity.state or entity.attributes.get("path")
                if path and (match := re.search(r"/view-assist/([^/]+)", path)):
                    return match.group(1)

        return None

    def _setup_timeout(self, entity_id: str, timeout: int) -> None:
        """Setup timeout for menu."""
        menu_state = self._get_or_create_state(entity_id)

        if menu_state.menu_timeout and not menu_state.menu_timeout.done():
            menu_state.menu_timeout.cancel()

        menu_state.menu_timeout = self.hass.async_create_task(
            self._timeout_task(entity_id, timeout)
        )

    async def _timeout_task(self, entity_id: str, timeout: int) -> None:
        """Task to handle menu timeout."""
        await self._handle_timeout(
            lambda: self.toggle_menu(entity_id, False),
            timeout
        )

    def _cancel_timeout(self, entity_id: str) -> None:
        """Cancel any existing timeout for an entity."""
        menu_state = self._get_or_create_state(entity_id)

        if menu_state.menu_timeout and not menu_state.menu_timeout.done():
            menu_state.menu_timeout.cancel()
            menu_state.menu_timeout = None

    async def _setup_item_timeout(self, entity_id: str, menu_item: str, timeout: int, is_menu_item: bool = False) -> None:
        """Set up a timeout for a specific menu item."""
        menu_state = self._get_or_create_state(entity_id)

        prefix = "menu_" if is_menu_item else "status_"
        item_key = f"{prefix}{menu_item}"

        self._cancel_item_timeout(entity_id, menu_item, is_menu_item)

        menu_state.item_timeouts[item_key] = self.hass.async_create_task(
            self._item_timeout_task(
                entity_id, menu_item, timeout, is_menu_item)
        )

    async def _item_timeout_task(self, entity_id: str, menu_item: str, timeout: int, is_menu_item: bool = False) -> None:
        """Task to handle individual menu item timeout."""
        await self._handle_timeout(
            lambda: self.remove_menu_item(entity_id, menu_item, is_menu_item),
            timeout
        )

    def _cancel_item_timeout(self, entity_id: str, menu_item: str, is_menu_item: bool = False) -> None:
        """Cancel timeout for a specific menu item."""
        menu_state = self._get_or_create_state(entity_id)

        prefix = "menu_" if is_menu_item else "status_"
        item_key = f"{prefix}{menu_item}"

        if task := menu_state.item_timeouts.get(item_key):
            if not task.done():
                task.cancel()
            menu_state.item_timeouts.pop(item_key, None)

    async def _handle_timeout(self, callback: Callable, timeout: int) -> None:
        """Generic timeout handling for menu operations."""
        try:
            await asyncio.sleep(timeout)
            await callback()
        except asyncio.CancelledError:
            pass

    def _normalize_status_items(self, raw_input: Any) -> List[str]:
        """Normalize and validate status items input."""
        result = normalize_status_items(raw_input)

        if isinstance(result, str):
            return [result]
        elif result is None:
            return []
        return result

    async def _update_entity_state(self, entity_id: str, changes: Dict[str, Any]) -> None:
        """Update entity state with changes, batching updates when possible."""
        if not changes:
            return

        if entity_id not in self._pending_updates:
            self._pending_updates[entity_id] = {}

        self._pending_updates[entity_id].update(changes)

        if not self._update_task or self._update_task.done():
            self._update_task = self.hass.async_create_task(
                self._process_pending_updates()
            )

    async def _process_pending_updates(self) -> None:
        """Process all pending entity state updates."""
        await asyncio.sleep(0.01)

        updates = self._pending_updates.copy()
        self._pending_updates.clear()

        for entity_id, changes in updates.items():
            if not changes:
                continue

            changes["entity_id"] = entity_id
            await self.hass.services.async_call(DOMAIN, "set_state", changes)
