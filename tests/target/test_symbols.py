"""ElfSymbolResolver against a real firmware build.

The fixture is the checked-in Release ELF of ../f401-USBsim -- a genuine
arm-none-eabi output, not a hand-assembled one, so the parser is held to what
a toolchain actually emits. It is committed rather than read from the sibling
project so this test needs neither that checkout nor a build.

`usbh_sim` is the MMB the IO server has to locate; `Appli_state` is the
application-state global the hardware tests read.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from target.symbols import ElfSymbolResolver

ELF = Path(__file__).parent / "fixtures" / "f401_usbsim.elf"

USBH_SIM_ADDR = 0x200000EC
APPLI_STATE_ADDR = 0x2000055C


@pytest.fixture
def resolver():
    return ElfSymbolResolver(ELF)


def test_resolves_the_mmb_symbol(resolver):
    symbol = resolver.resolve("usbh_sim")

    assert symbol is not None
    assert symbol.address == USBH_SIM_ADDR
    assert symbol.name == "usbh_sim"
    assert symbol.source == f"elf:{ELF}"


def test_resolves_other_globals_from_the_same_table(resolver):
    assert resolver.resolve("Appli_state").address == APPLI_STATE_ADDR


def test_the_mmb_lands_in_ram(resolver):
    # The F401RC has 64K of RAM. The MMB is a .bss global, so an address
    # outside that window means the parser read the wrong field.
    assert 0x20000000 <= resolver.resolve("usbh_sim").address < 0x20010000


def test_absent_symbol_resolves_to_none(resolver):
    assert resolver.resolve("not_a_symbol") is None


def test_a_file_that_is_not_an_elf_yields_no_symbols(tmp_path):
    not_elf = tmp_path / "firmware.map"
    not_elf.write_text("Archive member included to satisfy reference by file\n")

    assert ElfSymbolResolver(not_elf).resolve("usbh_sim") is None


def test_a_64_bit_elf_yields_no_symbols(tmp_path):
    wrong_class = bytearray(ELF.read_bytes())
    wrong_class[4] = 2  # e_ident[EI_CLASS] = ELFCLASS64
    elf = tmp_path / "elf64.elf"
    elf.write_bytes(wrong_class)

    assert ElfSymbolResolver(elf).resolve("usbh_sim") is None


def test_a_big_endian_elf_yields_no_symbols(tmp_path):
    wrong_endian = bytearray(ELF.read_bytes())
    wrong_endian[5] = 2  # e_ident[EI_DATA] = ELFDATA2MSB
    elf = tmp_path / "elfbe.elf"
    elf.write_bytes(wrong_endian)

    assert ElfSymbolResolver(elf).resolve("usbh_sim") is None


def test_a_truncated_elf_yields_no_symbols(tmp_path):
    elf = tmp_path / "truncated.elf"
    elf.write_bytes(ELF.read_bytes()[:60])  # past the header, into no sections

    assert ElfSymbolResolver(elf).resolve("usbh_sim") is None


def test_a_missing_file_yields_no_symbols(tmp_path):
    assert ElfSymbolResolver(tmp_path / "nope.elf").resolve("usbh_sim") is None


def test_the_elf_is_read_once(tmp_path):
    copy = tmp_path / "firmware.elf"
    copy.write_bytes(ELF.read_bytes())
    resolver = ElfSymbolResolver(copy)

    assert resolver.resolve("usbh_sim").address == USBH_SIM_ADDR
    copy.unlink()

    assert resolver.resolve("usbh_sim").address == USBH_SIM_ADDR
