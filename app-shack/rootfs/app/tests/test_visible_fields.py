"""HA 2026.11 conditional form fields (``visible=``) and form sections."""

import sys
from pathlib import Path

import probatio

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.config_entries import strip_hidden_fields


def test_flow_markers_are_importable_and_keep_the_condition():
    """Integrations import the markers from ``homeassistant.data_entry_flow``."""
    from homeassistant import data_entry_flow
    from homeassistant.helpers.selector import BooleanSelector

    marker = data_entry_flow.Required(
        "verify_ssl", default=True, visible={"field": "ssl", "value": True}
    )
    assert marker.schema == "verify_ssl"
    assert marker.visible == {"field": "ssl", "value": True}
    assert isinstance(marker, probatio.Required)

    optional = data_entry_flow.Optional(
        "port", visible={"field": "ssl", "value": True}
    )
    assert isinstance(optional, probatio.Optional)
    assert data_entry_flow.section is not None
    assert BooleanSelector is not None


def test_section_is_callable_and_tracks_collapsed():
    from homeassistant.data_entry_flow import section

    schema = probatio.Schema({probatio.Required("host"): str})
    grouped = section(schema, {"collapsed": True})
    assert grouped({"host": "example"}) == {"host": "example"}
    assert grouped.options["collapsed"] is True


def test_hidden_field_is_dropped_from_the_submission():
    """A field whose condition no longer matches does not reach the step."""
    from homeassistant.data_entry_flow import Required

    schema = probatio.Schema(
        {
            probatio.Required("ssl"): bool,
            Required("verify_ssl", visible={"field": "ssl", "value": True}): bool,
        }
    )

    _schema, cleaned = strip_hidden_fields(
        schema, {"ssl": False, "verify_ssl": True}
    )
    assert cleaned == {"ssl": False}

    # Visible: the value is kept.
    _schema, cleaned = strip_hidden_fields(schema, {"ssl": True, "verify_ssl": True})
    assert cleaned == {"ssl": True, "verify_ssl": True}


def test_visibility_uses_defaults_for_omitted_controlling_fields():
    """An omitted controlling field resolves through its schema default."""
    from homeassistant.data_entry_flow import Required

    schema = probatio.Schema(
        {
            probatio.Required("ssl", default=False): bool,
            Required("verify_ssl", visible={"field": "ssl", "value": True}): bool,
        }
    )
    _schema, cleaned = strip_hidden_fields(schema, {"verify_ssl": True})
    assert cleaned == {}


def test_no_visible_conditions_leaves_the_input_alone():
    schema = probatio.Schema({probatio.Required("host"): str})
    user_input = {"host": "example"}
    assert strip_hidden_fields(schema, user_input) == (schema, user_input)
    assert strip_hidden_fields(None, user_input) == (None, user_input)
