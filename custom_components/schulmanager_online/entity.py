"""Base entity for Schulmanager Online."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SchulmanagerCoordinator, StudentData


class SchulmanagerEntity(CoordinatorEntity[SchulmanagerCoordinator]):
    """Common device info and attribution."""

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by Schulmanager Online"

    def __init__(self, coordinator: SchulmanagerCoordinator, student_id: str) -> None:
        """Group all entities of one student under a single device."""
        super().__init__(coordinator)
        self._student_id = student_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, student_id)},
            manufacturer="Schulmanager Online",
            name=self.student.name,
        )

    @property
    def student(self) -> StudentData:
        """Return this entity's student."""
        return self.coordinator.data.students[self._student_id]

    @property
    def available(self) -> bool:
        """Only report available while the student is still in the payload."""
        return super().available and self._student_id in self.coordinator.data.students
