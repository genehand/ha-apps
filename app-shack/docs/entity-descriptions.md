# Entity Descriptions and FrozenOrThawed

When creating `EntityDescription` subclasses for platform entities, use the
`FrozenOrThawed` metaclass with `frozen_or_thawed=True`:

```python
from ..entity import EntityDescription
from ..frozen_dataclass_compat import FrozenOrThawed

class MyEntityDescription(EntityDescription, metaclass=FrozenOrThawed, frozen_or_thawed=True):
    """Description for my entity type."""

    custom_field: Optional[str] = None
```

**Why use FrozenOrThawed?**

- `EntityDescription` uses `FrozenOrThawed` metaclass to create frozen dataclasses internally
- This allows child classes to be either frozen or non-frozen via standard `@dataclass` decorator
- All platform EntityDescriptions should use `frozen_or_thawed=True` for consistency with Home Assistant
- External integrations can use either `@dataclass(frozen=True)` or `@dataclass` (non-frozen)

**For external integrations:**

Use standard `@dataclass` decorator with either frozen or non-frozen:

```python
from dataclasses import dataclass
from homeassistant.backports.entity import EntityDescription

# Non-frozen (default) - for maximum compatibility
@dataclass
class CustomEntityDescription(EntityDescription):
    """Custom entity description."""
    custom_field: str = "default"

# Or frozen
@dataclass(frozen=True)
class FrozenEntityDescription(EntityDescription):
    """Frozen entity description."""
    custom_field: str = "default"
```

**Note:** The `FrozenOrThawed` metaclass works around Python dataclass inheritance
rules by creating frozen dataclasses internally while allowing subclasses to choose
their frozen status via `@dataclass` decorator.

The integration-compatibility cases this keeps working are listed in
[`testing.md`](testing.md#key-integration-patterns-to-test) — check them when
touching `shim/entity.py` or `shim/frozen_dataclass_compat.py`.
