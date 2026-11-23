"""Register SystemDesk tools by importing their owning modules."""

from . import lifecycle, projects, autosar, autosar_composition, create_vecu, vecu_configuration, vecu_export

__all__ = ["lifecycle", "projects", "autosar", "autosar_composition", "create_vecu", "vecu_configuration", "vecu_export"]
