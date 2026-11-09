"""Register SystemDesk tools by importing their owning modules."""

from . import lifecycle, projects, autosar, autosar_composition

__all__ = ["lifecycle", "projects", "autosar", "autosar_composition"]
