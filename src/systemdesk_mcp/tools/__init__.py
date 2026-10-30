"""Register SystemDesk tools by importing their owning modules."""

from . import autosar, create_vecu, lifecycle, projects

__all__ = ["autosar", "create_vecu", "lifecycle", "projects"]
