"""Connectivity must distinguish explicit offline from missing cloud data."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.seltron_clausius.binary_sensor import ConnectivitySensor
from custom_components.seltron_clausius.models import parse_installation_status
from custom_components.seltron_clausius.runtime import RuntimeData


@pytest.mark.parametrize("kind,field", [("gateway", "connectionState"), ("controller", "isActive")])
@pytest.mark.parametrize(
    "raw,expected",
    [
        ({}, None),
        ({"value": None}, None),
        ({"value": "false"}, None),
        ({"value": "true"}, None),
        ({"value": 0}, None),
        ({"value": 1}, None),
        ({"value": {}}, None),
        ({"value": []}, None),
        ({"value": False}, False),
        ({"value": True}, True),
    ],
)
def test_connectivity_preserves_only_explicit_boolean(kind, field, raw, expected):
    payload = {field: raw["value"]} if raw else {}
    status = parse_installation_status(
        payload if kind == "gateway" else {"connectionState": True},
        payload if kind == "controller" else {"isActive": True},
    )
    assert getattr(status, kind).connected is expected
    coordinator = SimpleNamespace(
        data=RuntimeData(status, datetime.now(UTC)),
        entry=SimpleNamespace(entry_id="synthetic-entry"),
        last_update_success=True,
    )
    sensor = ConnectivitySensor(coordinator, kind)
    assert sensor.is_on is expected
    assert sensor.state == (None if expected is None else "on" if expected else "off")


def test_gateway_poll_sequence_does_not_invent_disconnects_or_keep_stale_online():
    coordinator = SimpleNamespace(
        entry=SimpleNamespace(entry_id="synthetic-entry"),
        last_update_success=True,
    )
    sensor = ConnectivitySensor(coordinator, "gateway")
    states = []
    for gateway in (
        {"connectionState": True}, {}, {"connectionState": True},
        {"connectionState": False}, {"connectionState": True},
    ):
        coordinator.data = RuntimeData(
            parse_installation_status(gateway, {"isActive": True}), datetime.now(UTC)
        )
        states.append(sensor.state)
    assert states == ["on", None, "on", "off", "on"]
    coordinator.last_update_success = False
    assert sensor.available is False
