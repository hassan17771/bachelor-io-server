import pytest

from engine import registry
from engine.context import TestContext
from tests.support.fake_gateway import FakeMmbGateway


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def context():
    return TestContext(
        soc=None,
        gateway=FakeMmbGateway(),
        peripherals={"usb_host": object()},
    )


def test_lookups_return_the_installed_objects(context):
    registry.install(context)

    assert registry.gateway() is context.gateway
    assert registry.peripheral("usb_host") is context.peripherals["usb_host"]


def test_reset_clears_the_installed_context(context):
    registry.install(context)
    registry.reset()

    with pytest.raises(LookupError):
        registry.gateway()
    with pytest.raises(LookupError):
        registry.peripheral("usb_host")


def test_lookups_fail_before_anything_is_installed():
    with pytest.raises(LookupError):
        registry.gateway()
    with pytest.raises(LookupError):
        registry.peripheral("usb_host")


def test_unknown_peripheral_name_names_what_is_configured(context):
    registry.install(context)

    with pytest.raises(LookupError, match="usb_host"):
        registry.peripheral("spi_flash")


def test_install_replaces_a_previous_context(context):
    registry.install(context)
    other = TestContext(soc=None, gateway=FakeMmbGateway(), peripherals={})
    registry.install(other)

    assert registry.gateway() is other.gateway
