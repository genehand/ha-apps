"""Utility for frozen/thawed dataclass compatibility.

Simplified version of Home Assistant's frozen_dataclass_compat.
"""

from annotationlib import Format, get_annotations
import dataclasses
from typing import Any


class FrozenOrThawed(type):
    """Metaclass which allows frozen or mutable dataclasses to be derived.

    This allows child classes to be either mutable or frozen dataclasses.
    """

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[Any, Any],
        frozen_or_thawed: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Create new class."""
        namespace["_frozen_or_thawed"] = frozen_or_thawed
        return super().__new__(mcs, name, bases, namespace)

    def __init__(
        cls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[Any, Any],
        **kwargs: Any,
    ) -> None:
        """Initialize the class.

        If frozen_or_thawed is set, create a dataclass and set up __init__ and __new__.
        If not, inject parent annotations to allow dataclass inheritance.
        """
        if namespace.get("_frozen_or_thawed"):
            # This class uses frozen_or_thawed, create a dataclass
            # Collect annotations from parents first (in reverse MRO order), then this class
            # This ensures parent fields come before child fields
            annotations: dict[str, Any] = {}
            # Process in reverse MRO so base classes come first
            for parent in reversed(cls.__mro__):
                if parent is object or parent is cls:
                    continue
                try:
                    parent_annotations = get_annotations(
                        parent, format=Format.FORWARDREF
                    )
                    # Only add annotations that aren't already defined
                    for key, value in parent_annotations.items():
                        if key not in annotations:
                            annotations[key] = value
                except Exception:
                    pass

            # Now add this class's annotations (they come after parent annotations)
            try:
                class_annotations = get_annotations(cls, format=Format.FORWARDREF)
                for key, value in class_annotations.items():
                    if key not in annotations:
                        annotations[key] = value
            except Exception:
                pass

            # Create the dataclass fields with defaults
            fields = []
            field_defaults = {
                "device_class": None,
                "entity_category": None,
                "entity_registry_enabled_default": True,
                "entity_registry_visible_default": True,
                "has_entity_name": False,
                "icon": None,
                "name": None,
                "translation_key": None,
                "unit_of_measurement": None,
                "state_class": None,
                "native_unit_of_measurement": None,
                "options": None,
                "preset_modes": None,
                "supported_features": 0,
                # Text entity description fields
                "pattern": None,
                "mode": "text",
                "min": 0,
                "max": 255,
            }
            for field_name, field_type in annotations.items():
                if field_name in field_defaults:
                    fields.append((field_name, field_type, field_defaults[field_name]))
                else:
                    fields.append((field_name, field_type))

            # Create a dataclass with the annotations
            dataclass_cls = dataclasses.make_dataclass(
                name,
                fields,
                frozen=True,
            )

            # Store the dataclass and set up __init__ and __new__
            cls._dataclass = dataclass_cls
            cls.__init__ = dataclass_cls.__init__

            def __new__(cls_, *args, **kwargs):
                """Create instance using the dataclass."""
                if dataclasses.is_dataclass(cls_):
                    return object.__new__(cls_)
                return cls_._dataclass(*args, **kwargs)

            cls.__new__ = __new__
        else:
            # This class is a real dataclass, inject parent's annotations
            annotations = {}
            for parent in cls.__mro__[::-1]:
                if parent is object:
                    continue
                try:
                    parent_annotations = get_annotations(
                        parent, format=Format.FORWARDREF
                    )
                    annotations |= parent_annotations
                except Exception:
                    pass

            if annotations:
                if "__annotations__" in cls.__dict__:
                    cls.__annotations__ = annotations
                else:

                    def wrapped_annotate(format: Format) -> dict[str, Any]:
                        return annotations

                    cls.__annotate__ = wrapped_annotate
