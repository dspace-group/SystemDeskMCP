"""Register SystemDesk tools by importing their owning modules."""

from . import lifecycle, projects, autosar, create_vecu

__all__ = ["lifecycle", "projects", "autosar", "create_vecu"]
