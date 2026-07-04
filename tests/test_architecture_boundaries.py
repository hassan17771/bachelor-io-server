"""Import-boundary rules for the peripherals/usb package tree.

The layering is convention until something checks it; this test is that check
(Task 4.1 of docs/plan/usb-peripheral-restructure.md). Rules enforced:

    1. The generic host layer (``peripherals/usb/host/abi.py``, ``core.py``,
       ``cdc.py``) never imports a concrete backend
       (``peripherals/usb/host/backends/*``). Backends depend on the ABC,
       never the other way around.
    2. The class-agnostic constants (``peripherals/usb/common/*``) never
       import the host layer (``peripherals/usb/host/*``) -- a future
       device-mode or MSC/HID driver must be able to use them standalone.

The check is static: each module's AST is scanned for ``import`` /
``from ... import`` statements (relative imports resolved against the
module's package), so it needs no import-linter dependency and cannot be
fooled by import-time side effects.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Iterator, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent


def _module_name(path: Path) -> str:
    """peripherals/usb/host/core.py -> peripherals.usb.host.core"""
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports_of(path: Path) -> Iterator[Tuple[int, str]]:
    """Yield (lineno, absolute_module_name) for every import in the file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    package_parts = _module_name(path).split(".")
    if not path.name == "__init__.py":
        package_parts = package_parts[:-1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                # Resolve "from ..x import y" against the containing package.
                anchor = package_parts[: len(package_parts) - (node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            # "from base import y" may name a submodule base.y; report both so
            # a prefix rule catches either form.
            yield node.lineno, base
            for alias in node.names:
                yield node.lineno, f"{base}.{alias.name}" if base else alias.name


def _violations(files: List[Path], forbidden_prefix: str) -> List[str]:
    found = []
    for path in files:
        for lineno, module in _imports_of(path):
            if module == forbidden_prefix or module.startswith(forbidden_prefix + "."):
                found.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno} imports {module}"
                )
    return found


def _tree(rel: str) -> List[Path]:
    files = sorted((REPO_ROOT / rel).rglob("*.py"))
    assert files, f"no Python files under {rel} -- did the tree move?"
    return files


class UsbImportBoundariesTest(unittest.TestCase):
    def test_generic_host_layer_does_not_import_backends(self) -> None:
        generic = [
            REPO_ROOT / "peripherals/usb/host/__init__.py",
            REPO_ROOT / "peripherals/usb/host/abi.py",
            REPO_ROOT / "peripherals/usb/host/core.py",
            REPO_ROOT / "peripherals/usb/host/cdc.py",
        ]
        for path in generic:
            self.assertTrue(path.is_file(), f"{path} missing -- did the tree move?")
        bad = _violations(generic, "peripherals.usb.host.backends")
        self.assertEqual(
            bad, [],
            "the generic host layer must stay backend-agnostic "
            "(backends import the ABC, never the reverse):\n  " + "\n  ".join(bad),
        )

    def test_common_does_not_import_host(self) -> None:
        bad = _violations(_tree("peripherals/usb/common"), "peripherals.usb.host")
        self.assertEqual(
            bad, [],
            "peripherals/usb/common must stay class- and role-agnostic:\n  "
            + "\n  ".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
