"""Register SystemDesk tools by importing their owning modules."""

from . import lifecycle, projects, autosar, vecu_configuration

__all__ = ["lifecycle", "projects", "autosar", "vecu_configuration"]
