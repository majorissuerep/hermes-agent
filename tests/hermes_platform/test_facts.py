from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from hermes_platform.host import facts

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AMD64", "amd64"),
        ("x86_64", "amd64"),
        ("aarch64", "arm64"),
        ("ARM64", "arm64"),
        ("i386", "x86"),
        ("weird", "unknown"),
    ],
)
def test_normalize_arch(raw: str, expected: str) -> None:
    assert facts.normalize_arch(raw) == expected

@pytest.mark.parametrize(
    ("cpuinfo", "vendor"),
    [
        ("processor : 0\nvendor_id : GenuineIntel\nmodel name : Intel Xeon\n", "GenuineIntel"),
        ("processor : 0\nCPU implementer : 0x41\nCPU part : 0xd87\n", "ARM"),
        ("processor : 0\nCPU implementer : 0x61\n", "Apple"),
        ("processor : 0\nCPU implementer : 0xee\n", ""),
        ("processor : 0\n", ""),
    ],
)
def test_cpu_vendor_from_cpuinfo(cpuinfo: str, vendor: str) -> None:
    assert facts.parse_cpuinfo_vendor(cpuinfo) == vendor

@pytest.mark.parametrize(
    ("text", "device_tree_model", "expected"),
    [
        (
            "Hardware : Fallback board\nmodel name : Primary CPU\n",
            "Ignored_Model\0",
            "Primary CPU",
        ),
        ("processor : 0\nHardware : ARM Board\n", "Ignored_Model\0", "ARM Board"),
        ("processor : 0\n", "Vendor_Board_Name\0", "Vendor Board Name"),
    ],
)
def test_parse_cpuinfo_fallbacks(
    text: str,
    device_tree_model: str,
    expected: str,
) -> None:
    assert facts.parse_cpuinfo(text, device_tree_model=device_tree_model) == expected

@pytest.fixture
def cleared_fact_caches():
    facts.clear_caches()
    yield
    facts.clear_caches()
@pytest.mark.platforms("macos")
def test_macos_live_cpu_facts(cleared_fact_caches) -> None:
    assert facts.cpu_model()
    assert facts.cpu_vendor()
    assert facts.native_arch() in {"arm64", "amd64"}
    assert facts.gpu_class() != "unknown"

@pytest.mark.platforms("linux")
def test_linux_live_cpu_facts_match_cpuinfo(cleared_fact_caches) -> None:
    cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")

    assert facts.cpu_vendor() == facts.parse_cpuinfo_vendor(cpuinfo)
    assert facts.cpu_model() in cpuinfo
