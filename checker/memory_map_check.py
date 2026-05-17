#!/usr/bin/env python3
"""
memory_map_check.py — Verify RT-Thread/GD32 memory map consistency.

Reads ELF symbols (_end, _ebss, _sidata, HEAP_BEGIN, etc.) and validates:
  1. Heap total size calculation
  2. Heap is within SRAM1 range (>= 0x20020000)
  3. .bss does not overlap DMA buffer region
  4. .sram1_bss section is 32-byte aligned
  5. No overlap between sections

Usage:
    python3 memory_map_check.py firmware.elf
    python3 memory_map_check.py --dump firmware.elf
"""

import struct
import sys
import os
from pathlib import Path

# ── Expected memory layout (GD32F303 / STM32F4-like) ──────────────────────
#
# Region      Start       End          Size
# ─────────────────────────────────────────
# SRAM0       0x20000000  0x2000FFFF   64 KB
# SRAM1       0x20010000  0x2001FFFF   64 KB  (CCM / DMA buffer region)
# SRAM2       0x20020000  0x2003FFFF  128 KB  (main heap region)
#
# Typical GD32F4xx mapping:
#  - .data / .bss in SRAM0
#  - DMA buffer in SRAM1
#  - Heap starts at SRAM2 or after .bss/.data in SRAM0

SRAM0_START = 0x20000000
SRAM0_END   = 0x2000FFFF

SRAM1_START = 0x20010000
SRAM1_END   = 0x2001FFFF  # DMA buffer region

SRAM2_START = 0x20020000
SRAM2_END   = 0x2003FFFF  # Main heap region

DMA_BUFFER_START = 0x20010000
DMA_BUFFER_END   = 0x20017FFF  # 32 KB reserved for DMA


# ── Section information (expected symbols from link script) ───────────────

REQUIRED_SYMBOLS = {
    "_end":       "End of .bss / start of heap (or end of all static data)",
    "_ebss":      "End of .bss section",
    "_sidata":    "Start of .data init values in flash",
    "_sdata":     "Start of .data in SRAM",
    "_edata":     "End of .data in SRAM",
    "_sbss":      "Start of .bss in SRAM",
    "_ebss":      "End of .bss in SRAM",
    "HEAP_BEGIN": "Start of heap (alias or variable, may be same as _end)",
    "HEAP_END":   "End of heap (or __heap_end / __StackTop)",
}

OPTIONAL_SYMBOLS = {
    "__DMA_Buffer_start": "Start of DMA buffer region",
    "__DMA_Buffer_end":   "End of DMA buffer region",
    "_sram1_bss_start":   "Start of .sram1_bss section",
    "_sram1_bss_end":     "End of .sram1_bss section",
    "__StackTop":          "Top of main stack",
    "__stack_start":       "Start of stack region",
    "heap_end":            "Alternative heap end symbol",
}


def _read_elf_symbols(elf_path: str) -> dict:
    """
    Read ELF symbol values using 'objdump' or 'readelf'.
    Returns dict of symbol_name -> address (int).
    """
    symbols = {}

    # Try readelf first (more portable)
    try:
        import subprocess
        # Method 1: readelf -s
        result = subprocess.run(
            ["readelf", "-s", elf_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 8 and parts[3] == "OBJECT":
                    # readelf -s format:
                    # Num: Value  Size Type  Bind  Vis  Ndx  Name
                    # Typically Value is at index 1, Name at index 7
                    try:
                        addr = int(parts[1], 16) if parts[1].startswith("0") or \
                               len(parts[1]) == 8 else int(parts[1], 16)
                        name = parts[7]
                        symbols[name] = addr
                    except (ValueError, IndexError):
                        pass
            if symbols:
                return symbols
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Method 2: objdump -t
    try:
        result = subprocess.run(
            ["objdump", "-t", elf_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                # objdump format: ADDR g/F l? OPR? SYMBOL_TYPE SECTION NAME
                # e.g.: 20020000 g     O .bss   00000004 _end
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        addr = int(parts[0], 16)
                        name = parts[-1]
                        # Filter for global/defined symbols
                        symbols[name] = addr
                    except (ValueError, IndexError):
                        pass
            if symbols:
                return symbols
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Method 3: nm -n (numeric sort by address)
    try:
        result = subprocess.run(
            ["nm", "-n", elf_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        addr = int(parts[0], 16)
                        sym_type = parts[1]
                        name = parts[2]
                        if sym_type in ("A", "T", "t", "D", "d", "B", "b", "C", "V", "v"):
                            symbols[name] = addr
                    except (ValueError, IndexError):
                        pass
            return symbols
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return symbols


def _read_elf_sections(elf_path: str) -> list:
    """
    Read section headers from ELF file using readelf -S.
    Returns list of (name, addr, size, flags) tuples.
    """
    sections = []

    try:
        import subprocess
        result = subprocess.run(
            ["readelf", "-S", elf_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Parse section headers
            # Format: [Nr] Name       Type       Addr     Off    Size   ES Flg Lk Inf Al
            for line in result.stdout.splitlines():
                if "PROGBITS" not in line and "NOBITS" not in line:
                    continue
                parts = line.split()
                if len(parts) >= 7:
                    try:
                        name = parts[1].strip("[]")
                        # Sometimes name is in [Nr] field... handle readelf variants
                        if name.startswith("."):
                            # Format: [ 4] .bss      NOBITS  20020000 001000 000400 00 WA 0 0 32
                            addr = int(parts[3], 16)
                            size = int(parts[5], 16)
                            flags = parts[7] if len(parts) > 7 else ""
                            sections.append((name, addr, size, flags))
                    except (ValueError, IndexError):
                        pass
            return sections
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return sections


def check_heap_size(symbols: dict) -> dict:
    """
    Calculate heap size from ELF symbols and validate it.
    Returns dict with results.
    """
    result = {
        "check": "Heap size calculation",
        "status": "FAIL",
        "details": [],
    }

    # Get key addresses
    _end = symbols.get("_end", 0)
    _ebss = symbols.get("_ebss", 0)
    heap_begin = symbols.get("HEAP_BEGIN", 0)
    heap_end = symbols.get("HEAP_END", 0)

    # Determine actual heap start
    if heap_begin:
        heap_start = heap_begin
    elif _end:
        heap_start = _end
    elif _ebss:
        heap_start = _ebss
    else:
        result["details"].append("Cannot determine heap start: no _end, _ebss, or HEAP_BEGIN")
        return result

    # Determine actual heap end
    if heap_end:
        heap_stop = heap_end
    elif "__StackTop" in symbols:
        heap_stop = symbols["__StackTop"]
    elif "_estack" in symbols:
        heap_stop = symbols["_estack"]
    else:
        heap_stop = SRAM2_END  # default

    heap_total = heap_stop - heap_start

    result["details"].append(f"heap_start  = 0x{heap_start:08X}")
    result["details"].append(f"heap_end    = 0x{heap_stop:08X}")
    result["details"].append(f"heap_size   = {heap_total} bytes ({heap_total / 1024:.1f} KB)")

    if heap_total <= 0:
        result["details"].append("ERROR: Heap size is zero or negative")
        result["status"] = "FAIL"
    elif heap_total > 1024 * 1024:
        result["details"].append(f"WARNING: Heap size ({heap_total / 1024:.0f} KB) seems excessive")
        result["status"] = "WARN"
    elif heap_total < 4096:
        result["details"].append(f"WARNING: Heap size ({heap_total} bytes) is very small")
        result["status"] = "WARN"
    else:
        result["status"] = "PASS"

    return result


def check_heap_in_sram2(symbols: dict) -> dict:
    """
    Verify heap resides in SRAM2 range (>= 0x20020000).
    """
    result = {
        "check": "Heap must be >= SRAM2 base (0x20020000)",
        "status": "FAIL",
        "details": [],
    }

    heap_begin = symbols.get("HEAP_BEGIN", 0) or symbols.get("_end", 0) or symbols.get("_ebss", 0)

    result["details"].append(f"HEAP_BEGIN / _end = 0x{heap_begin:08X}")

    if heap_begin == 0:
        result["details"].append("Cannot determine heap address")
        return result

    if heap_begin >= SRAM2_START:
        result["details"].append(f"OK: 0x{heap_begin:08X} >= 0x{SRAM2_START:08X} (SRAM2)")
        result["status"] = "PASS"
    elif heap_begin >= SRAM1_START:
        result["details"].append(f"WARNING: Heap 0x{heap_begin:08X} is in SRAM1, not SRAM2")
        result["status"] = "WARN"
    else:
        result["details"].append(f"ERROR: Heap 0x{heap_begin:08X} is below SRAM1!")
        result["status"] = "FAIL"

    return result


def check_bss_no_dma_overlap(symbols: dict, sections: list) -> dict:
    """
    Verify .bss section does not overlap the DMA buffer region.
    DMA buffer is typically in SRAM1 (0x20010000 - 0x20017FFF).
    """
    result = {
        "check": ".bss does not overlap DMA buffer (SRAM1: 0x20010000-0x20017FFF)",
        "status": "PASS",
        "details": [],
    }

    # Get .bss address from sections
    bss_addr = 0
    bss_size = 0
    for name, addr, size, flags in sections:
        if name == ".bss":
            bss_addr = addr
            bss_size = size
            break

    if bss_addr == 0:
        # Try from symbols
        sbss = symbols.get("_sbss", 0)
        ebss = symbols.get("_ebss", 0)
        if sbss and ebss:
            bss_addr = sbss
            bss_size = ebss - sbss

    if bss_addr == 0 or bss_size == 0:
        result["details"].append("Cannot locate .bss section")
        result["status"] = "SKIP"
        return result

    bss_end = bss_addr + bss_size

    result["details"].append(f".bss:  0x{bss_addr:08X} - 0x{bss_end:08X}  ({bss_size} bytes)")

    # Check overlap with DMA buffer
    overlap_start = max(bss_addr, DMA_BUFFER_START)
    overlap_end   = min(bss_end, DMA_BUFFER_END)

    if overlap_start < overlap_end:
        overlap_size = overlap_end - overlap_start
        result["details"].append(
            f"OVERLAP: {overlap_size} bytes overlap with DMA buffer "
            f"(0x{overlap_start:08X} - 0x{overlap_end:08X})")
        result["status"] = "FAIL"
    else:
        result["details"].append(
            f"OK: No overlap with DMA buffer (0x{DMA_BUFFER_START:08X} - 0x{DMA_BUFFER_END:08X})")

    # Also check general SRAM1 region
    sram1_overlap_start = max(bss_addr, SRAM1_START)
    sram1_overlap_end   = min(bss_end, SRAM1_END)

    if sram1_overlap_start < sram1_overlap_end:
        overlap_sz = sram1_overlap_end - sram1_overlap_start
        result["details"].append(
            f"INFO: .bss occupies {overlap_sz} bytes in SRAM1 "
            f"(0x{sram1_overlap_start:08X} - 0x{sram1_overlap_end:08X})")
        if result["status"] == "PASS":
            result["status"] = "INFO"

    return result


def check_sram1_bss_alignment(symbols: dict, sections: list) -> dict:
    """
    Verify .sram1_bss section is 32-byte aligned.
    """
    result = {
        "check": ".sram1_bss section is 32-byte aligned",
        "status": "FAIL",
        "details": [],
    }

    # Check sections
    sram1_bss_addr = 0
    for name, addr, size, flags in sections:
        if "sram1" in name.lower() and "bss" in name.lower():
            sram1_bss_addr = addr
            result["details"].append(f"Section '{name}': 0x{addr:08X} (size=0x{size:X})")
            break

    # Also check via symbols
    sbss1 = symbols.get("_sram1_bss_start", 0)
    if sbss1 and not sram1_bss_addr:
        sram1_bss_addr = sbss1
        result["details"].append(f"Symbol _sram1_bss_start: 0x{sbss1:08X}")

    if sram1_bss_addr == 0:
        result["details"].append(".sram1_bss section not found (optional, skipping)")
        result["status"] = "SKIP"
        return result

    # Check alignment
    if sram1_bss_addr % 32 == 0:
        result["details"].append(
            f"OK: 0x{sram1_bss_addr:08X} is 32-byte aligned "
            f"(0x{sram1_bss_addr:X} % 32 = {sram1_bss_addr % 32})")
        result["status"] = "PASS"
    else:
        result["details"].append(
            f"FAIL: 0x{sram1_bss_addr:08X} is NOT 32-byte aligned "
            f"(remainder = {sram1_bss_addr % 32})")
        result["status"] = "FAIL"

    return result


def check_section_overlaps(symbols: dict, sections: list) -> dict:
    """
    Check for any overlapping sections in the memory map.
    """
    result = {
        "check": "No overlapping loadable sections",
        "status": "PASS",
        "details": [],
    }

    # Sort sections by address
    loadable = [(name, addr, size) for name, addr, size, flags in sections
                if addr > 0 and size > 0 and "W" in flags]
    loadable.sort(key=lambda x: x[1])

    for i in range(len(loadable) - 1):
        name_i, addr_i, size_i = loadable[i]
        name_j, addr_j, size_j = loadable[i + 1]

        end_i = addr_i + size_i

        if end_i > addr_j:
            overlap = end_i - addr_j
            result["details"].append(
                f"OVERLAP: '{name_i}' (0x{addr_i:08X}-0x{end_i:08X}) "
                f"overlaps '{name_j}' (0x{addr_j:08X}) by {overlap} bytes")
            result["status"] = "FAIL"
        elif end_i == addr_j:
            result["details"].append(
                f"EDGE:    '{name_i}' ends at 0x{end_i:08X} == '{name_j}' start (contiguous)")

    if result["details"]:
        result["details"].insert(0, f"Checked {len(loadable)} writable sections")
    else:
        result["details"].append("No writable sections found to check")

    return result


def check_data_in_flash_and_ram(symbols: dict) -> dict:
    """
    Verify .data has corresponding flash (LMA) and RAM (VMA) addresses.
    """
    result = {
        "check": ".data LMA and VMA consistency",
        "status": "PASS",
        "details": [],
    }

    sidata = symbols.get("_sidata", 0)
    sdata  = symbols.get("_sdata", 0)
    edata  = symbols.get("_edata", 0)

    if not (sidata and sdata and edata):
        result["details"].append("Missing .data boundary symbols (optional check)")
        result["status"] = "SKIP"
        return result

    data_size_ram = edata - sdata
    result["details"].append(f"_sidata (flash) = 0x{sidata:08X}")
    result["details"].append(f"_sdata  (SRAM)  = 0x{sdata:08X}")
    result["details"].append(f"_edata  (SRAM)  = 0x{edata:08X}")
    result["details"].append(f".data size = {data_size_ram} bytes")

    if sidata < 0x08000000:
        result["details"].append("WARNING: _sidata not in flash region (< 0x08000000)")
        result["status"] = "WARN"

    if sdata < SRAM0_START or sdata > SRAM1_END:
        result["details"].append(f"WARNING: _sdata (0x{sdata:08X}) outside expected SRAM range")
        result["status"] = "WARN"

    return result


def run_all_checks(elf_path: str, dump_only: bool = False):
    """Run all memory map consistency checks."""

    if not os.path.exists(elf_path):
        print(f"ERROR: File not found: {elf_path}")
        return False

    print(f"Memory Map Checker")
    print(f"{'=' * 60}")
    print(f"ELF: {elf_path}")
    print()

    # Read symbols
    symbols = _read_elf_symbols(elf_path)
    sections = _read_elf_sections(elf_path)

    if not symbols:
        print("WARNING: No symbols found. Ensure readelf/objdump/nm are installed.")
        print()

    # ── Dump mode ──
    if dump_only:
        print("── Symbol dump ──")
        for name, addr in sorted(symbols.items(), key=lambda x: x[1]):
            print(f"  0x{addr:08X}  {name}")

        print()
        print("── Section dump ──")
        for name, addr, size, flags in sections:
            print(f"  0x{addr:08X}  [{flags}] {name}  (0x{size:X} bytes)")
        return True

    # ── Run checks ──
    checks = [
        check_heap_size(symbols),
        check_heap_in_sram2(symbols),
        check_bss_no_dma_overlap(symbols, sections),
        check_sram1_bss_alignment(symbols, sections),
        check_section_overlaps(symbols, sections),
        check_data_in_flash_and_ram(symbols),
    ]

    # Also print all detected symbols for reference
    print("── Key symbols found ──")
    for name in REQUIRED_SYMBOLS:
        if name in symbols:
            print(f"  0x{symbols[name]:08X}  {name}  // {REQUIRED_SYMBOLS[name]}")
    for name in OPTIONAL_SYMBOLS:
        if name in symbols:
            print(f"  0x{symbols[name]:08X}  {name}  // {OPTIONAL_SYMBOLS[name]}")

    print()
    print("── Check results ──")
    print()

    pass_count = 0
    fail_count = 0
    warn_count = 0

    for check in checks:
        status_char = {
            "PASS": "✓",
            "FAIL": "✗",
            "WARN": "⚠",
            "SKIP": "–",
            "INFO": "i",
        }.get(check["status"], "?")

        print(f"  [{status_char}] {check['check']}")
        for detail in check["details"]:
            print(f"         {detail}")
        print()

        if check["status"] == "PASS":
            pass_count += 1
        elif check["status"] == "FAIL":
            fail_count += 1
        elif check["status"] == "WARN":
            warn_count += 1

    # ── Summary ──
    print("── Summary ──")
    print(f"  PASS: {pass_count}")
    print(f"  WARN: {warn_count}")
    print(f"  FAIL: {fail_count}")
    print()

    if fail_count > 0:
        print("RESULT: FAIL — some checks did not pass.")
        return False
    elif warn_count > 0:
        print("RESULT: PASS with warnings — review suggested.")
        return True
    else:
        print("RESULT: PASS — all checks OK.")
        return True


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="RT-Thread/GD32 memory map consistency checker")
    parser.add_argument("elf", nargs="?", help="Path to ELF firmware file")
    parser.add_argument("--dump", action="store_true",
                        help="Dump symbols and sections, no checks")
    parser.add_argument("--list-symbols", action="store_true",
                        help="List all required/optional symbols")

    args = parser.parse_args()

    if args.list_symbols:
        print("Required symbols:")
        for name, desc in REQUIRED_SYMBOLS.items():
            print(f"  {name:20s} — {desc}")
        print()
        print("Optional symbols:")
        for name, desc in OPTIONAL_SYMBOLS.items():
            print(f"  {name:20s} — {desc}")
        return

    if args.elf:
        ok = run_all_checks(args.elf, dump_only=args.dump)
        sys.exit(0 if ok else 1)
    else:
        # Demo mode: simulate with known values
        print("No ELF specified. Running in demo/simulation mode.\n")
        sim_symbols = {
            "_end":       0x20020000,
            "_ebss":      0x2001FF00,
            "_sidata":    0x08010000,
            "_sdata":     0x20000000,
            "_edata":     0x20004000,
            "_sbss":      0x20004000,
            "HEAP_BEGIN": 0x20020000,
            "HEAP_END":   0x2003F000,
            "__StackTop": 0x2003FFFF,
        }
        sim_sections = [
            (".text",  0x08000000, 0x10000, "AX"),
            (".data",  0x20000000, 0x04000, "WA"),
            (".bss",   0x20004000, 0x1C000, "WA"),
            (".sram1_bss", 0x20010000, 0x2000, "WA"),
        ]

        print("── Simulated checks ──\n")

        for check_func in [
            check_heap_size,
            check_heap_in_sram2,
            check_bss_no_dma_overlap,
            check_sram1_bss_alignment,
            check_section_overlaps,
            check_data_in_flash_and_ram,
        ]:
            if check_func == check_section_overlaps:
                r = check_func(sim_symbols, sim_sections)
            elif check_func == check_bss_no_dma_overlap:
                r = check_func(sim_symbols, sim_sections)
            elif check_func == check_sram1_bss_alignment:
                r = check_func(sim_symbols, sim_sections)
            else:
                r = check_func(sim_symbols)
            print(f"  [{r['status']}] {r['check']}")
            for d in r["details"]:
                print(f"         {d}")
            print()


if __name__ == "__main__":
    main()
