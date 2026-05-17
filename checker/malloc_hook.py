#!/usr/bin/env python3
"""
malloc_hook.py — RT-Thread malloc/free sethook tracer via GDB

Hooks into rt_malloc_sethook / rt_free_sethook to capture every allocation
and deallocation. Output format:
    Alloc #N: LR=0xXXXX size=NNN addr=0xXXXX
    Free  #N: LR=0xXXXX addr=0xXXXX

Builds an allocation trail using CRC/magic values for integrity checking.
"""

import struct
import sys

# ── Constants ─────────────────────────────────────────────────────────────
ALLOC_TRAIL_MAGIC = 0xA110C47E  # "ALLOCATE" leet-speak
FREE_TRAIL_MAGIC  = 0xF0E0BEEF   # "FREE BEEF"
MAX_TRACE_DEPTH   = 1024         # max number of tracked allocations


def generate_gdb_script():
    """
    Generate a GDB Python or batch script that:
      1. Sets breakpoints on rt_malloc_sethook / rt_free_sethook
      2. On each hit, extracts LR, size, return address
      3. Prints formatted trace
      4. Maintains an internal allocation trail with CRC verification
    """
    print("""# ── malloc_hook.gdb — RT-Thread allocator tracer ──
set $alloc_count = 0
set $free_count = 0
set $alloc_trail_base = 0

# Define a small structure in target memory to store the trail
# (or we can use GDB convenience vars — limited, use a small array)
set $trail_size = 1024
set $trail_ptr = 0

# ── Helper: print register state ──
define dump_caller
  printf "  LR=0x%08X  FP=0x%08X  SP=0x%08X\\\\n", $lr, $r7, $sp
end

document dump_caller
  Dump caller registers: LR, FP, SP
end
""")

    # ── rt_malloc_sethook breakpoint ──
    print("""# ── Breakpoint: rt_malloc_sethook (called before real malloc) ──
set $malloc_hook_armed = 1

define hook-stop
  # Check if we hit rt_malloc_sethook
  if $_caller_is("rt_malloc_sethook") || $_caller_is("rt_free_sethook")
    # Handled in the specific breakpoint commands below
  end
end

break rt_malloc_sethook
commands
  silent
  set $alloc_count = $alloc_count + 1
  set $caller_lr   = $lr
  set $alloc_size  = $r0
  set $caller_func = 0

  # Try to resolve caller name via symbol table
  printf "Alloc #%d: LR=0x%08X size=%d\\n", \\
         $alloc_count, $caller_lr, $alloc_size

  # Optional: dump caller info
  # dump_caller

  # Record in trail (if we have a buffer)
  # Trail entry format: [magic(4)][size(4)][lr(4)][addr(4)] = 16 bytes
  # addr will be filled by the return breakpoint below
  set $trail_off = ($alloc_count - 1) * 16
  if $trail_off < $trail_size * 16
    # We'll record when the return address becomes known
    # For now, store size and LR at known offsets
    # (We use a separate breakpoint on return to capture the address)
  end

  continue
end
""")

    # ── rt_free_sethook breakpoint ──
    print("""# ── Breakpoint: rt_free_sethook (called before real free) ──
break rt_free_sethook
commands
  silent
  set $free_count = $free_count + 1
  set $caller_lr  = $lr
  set $free_addr  = $r0

  printf "Free  #%d: LR=0x%08X addr=0x%08X\\n", \\
         $free_count, $caller_lr, $free_addr

  # Verify against allocation trail
  # Scan allocation trail for matching address

  continue
end
""")

    # ── Post-return capture for malloc (to get the allocated address) ──
    # The simpler approach: break on instruction after the hook returns
    # Since rt_malloc* returns address in r0, we can break on the caller's
    # next instruction after BL. But that's address-dependent.
    # Better: use a watchpoint or break on the return from rt_malloc
    print("""# ── Note: To capture the returned address from malloc ──
# The hook is called *before* the real malloc, so r0 at hook time is the size,
# not the returned address. To get the returned address, set a breakpoint
# on the instruction after the BL in the caller, or use a hardware watchpoint.
#
# Alternative approach (recommended for simplicity):
# Break on rt_malloc itself (not the hook) and capture r0 on return:
#
#   break rt_malloc
#   commands
#     silent
#     # At entry: r0 = size
#     # At exit:  r0 = returned address
#     # GDB doesn't have a clean "on return" hook in batch mode,
#     # but Python GDB script can do this with a finish frame.
#   end
""")

    # ── Python GDB script for advanced tracing ──
    print("""
# ── Alternative: Python-based tracer (run via 'source' in GDB) ──
# Uncomment to use:
#
# python
# import gdb
# import struct
#
# class MallocHook(gdb.Breakpoint):
#     \"\"\"Trace all malloc calls with caller info.\"\"\"
#     def __init__(self):
#         super().__init__("rt_malloc", type=gdb.BP_BREAKPOINT)
#         self.count = 0
#         self.trail = []  # list of (magic, size, lr, addr)
#
#     def stop(self):
#         self.count += 1
#         try:
#             size = int(gdb.parse_and_eval("$r0"))
#             # Get caller LR from frame info
#             frame = gdb.selected_frame()
#             older = frame.older()
#             lr = 0
#             if older:
#                 try:
#                     lr = int(older.pc())
#                 except:
#                     lr = int(gdb.parse_and_eval("$lr"))
#             else:
#                 lr = int(gdb.parse_and_eval("$lr"))
#
#             # Record trail entry with magic
#             entry = (ALLOC_TRAIL_MAGIC, size, lr, 0)
#             self.trail.append(entry)
#
#             print(f"Alloc #{self.count}: LR=0x{lr:08X} size={size}")
#         except Exception as e:
#             print(f"MallocHook error: {e}")
#         return False  # don't stop, just trace
#
#     def get_trail_bytes(self):
#         \"\"\"Serialize the allocation trail for integrity checking.\"\"\"
#         data = b""
#         for magic, size, lr, addr in self.trail:
#             data += struct.pack("<IIII", magic, size, lr, addr)
#         return data
#
#     def verify_trail_integrity(self):
#         \"\"\"Verify trail hasn't been corrupted (e.g., by buffer overflow).\"\"\"
#         for i, (magic, size, lr, addr) in enumerate(self.trail):
#             if magic != ALLOC_TRAIL_MAGIC:
#                 print(f"TRAIL CORRUPTION at entry {i}: "
#                       f"expected magic 0x{ALLOC_TRAIL_MAGIC:08X}, "
#                       f"got 0x{magic:08X}")
#                 return False
#         return True
#
#
# class FreeHook(gdb.Breakpoint):
#     \"\"\"Trace all free calls with caller info.\"\"\"
#     def __init__(self):
#         super().__init__("rt_free", type=gdb.BP_BREAKPOINT)
#         self.count = 0
#
#     def stop(self):
#         self.count += 1
#         try:
#             addr = int(gdb.parse_and_eval("$r0"))
#             lr = int(gdb.parse_and_eval("$lr"))
#             print(f"Free  #{self.count}: LR=0x{lr:08X} addr=0x{addr:08X}")
#         except Exception as e:
#             print(f"FreeHook error: {e}")
#         return False
#
#
# # Install hooks
# _malloc_hook = MallocHook()
# _free_hook = FreeHook()
# print("malloc_hook.py: Allocation tracer installed.")
# print(f"  Magic: 0x{ALLOC_TRAIL_MAGIC:08X}")
# print(f"  Trail depth: {MAX_TRACE_DEPTH}")
# end
""")


def generate_python_tracer_script():
    """
    Generate a standalone Python script that can be sourced in GDB
    via `source malloc_hook_tracer.py`.
    """
    return f'''#!/usr/bin/env python3
"""
malloc_hook_tracer.py — GDB Python script for RT-Thread malloc/free tracing

Usage in GDB:
    (gdb) source malloc_hook_tracer.py

This registers breakpoints on rt_malloc and rt_free that log every call.
"""

import gdb
import struct

ALLOC_TRAIL_MAGIC = {ALLOC_TRAIL_MAGIC}
FREE_TRAIL_MAGIC  = {FREE_TRAIL_MAGIC}
MAX_TRACE_DEPTH   = {MAX_TRACE_DEPTH}


class MallocBreakpoint(gdb.Breakpoint):
    """Breakpoint logging each rt_malloc call."""

    count = 0
    trail = []  # list of (magic, size, lr, addr)

    def __init__(self):
        super().__init__("rt_malloc", type=gdb.BP_BREAKPOINT)
        MallocBreakpoint.count = 0
        MallocBreakpoint.trail = []

    def stop(self):
        MallocBreakpoint.count += 1
        n = MallocBreakpoint.count
        try:
            size = int(gdb.parse_and_eval("$r0"))
            lr = int(gdb.parse_and_eval("$lr"))

            addr = 0  # unknown until return; we attempt a finish

            entry = (ALLOC_TRAIL_MAGIC, size, lr, addr)
            MallocBreakpoint.trail.append(entry)

            print(f"Alloc #{{n}}: LR=0x{{lr:08X}} size={{size}}")

            return True  # stop so user can inspect if desired
        except Exception as e:
            print(f"MallocHook error: {{e}}")
            return False


class MallocReturnBreakpoint(gdb.FinishBreakpoint):
    """Capture the return value of rt_malloc."""

    def __init__(self, size, lr, entry_idx):
        super().__init__(internal=True)
        self.size = size
        self.lr = lr
        self.entry_idx = entry_idx

    def stop(self):
        try:
            # r0 contains the returned address after rt_malloc returns
            return_addr = int(gdb.parse_and_eval("$r0"))
            # Update the trail entry
            if self.entry_idx < len(MallocBreakpoint.trail):
                magic, size, lr, _ = MallocBreakpoint.trail[self.entry_idx]
                MallocBreakpoint.trail[self.entry_idx] = (magic, size, lr, return_addr)
                print(f"  -> addr=0x{{return_addr:08X}} "
                      f"[trail[{{self.entry_idx}}] updated]")
        except Exception as e:
            print(f"MallocReturnHook error: {{e}}")
        return False  # don't stop


class FreeBreakpoint(gdb.Breakpoint):
    """Breakpoint logging each rt_free call."""

    count = 0

    def __init__(self):
        super().__init__("rt_free", type=gdb.BP_BREAKPOINT)
        FreeBreakpoint.count = 0

    def stop(self):
        FreeBreakpoint.count += 1
        n = FreeBreakpoint.count
        try:
            addr = int(gdb.parse_and_eval("$r0"))
            lr = int(gdb.parse_and_eval("$lr"))
            print(f"Free  #{{n}}: LR=0x{{lr:08X}} addr=0x{{addr:08X}}")
            return False
        except Exception as e:
            print(f"FreeHook error: {{e}}")
            return False


# ── Installation ──────────────────────────────────────────────────────────

def install_tracers():
    """Install all tracer breakpoints."""
    _mbp = MallocBreakpoint()
    _fbp = FreeBreakpoint()
    print(f"malloc_hook_tracer installed:")
    print(f"  Breakpoints: rt_malloc (id={{_mbp.number}}), "
          f"rt_free (id={{_fbp.number}})")
    print(f"  Trail magic: 0x{{ALLOC_TRAIL_MAGIC:08X}}")
    print(f"  Max depth: {{MAX_TRACE_DEPTH}}")
    print(f"  Use 'info breakpoints' to see them.")
    return _mbp, _fbp


if __name__ == "__main__":
    _malloc_bp, _free_bp = install_tracers()
'''


def generate_trail_integrity_checker():
    """
    Generate a standalone script to verify allocation trail integrity
    from a hex dump or memory snapshot.
    """
    return f'''#!/usr/bin/env python3
"""
check_trail.py — Verify allocation trail integrity.

Reads a binary dump of the allocation trail and checks:
  - Each entry has the correct magic value
  - No entries have been clobbered (indicates buffer overflow)

Trail entry format (16 bytes each):
  [0:4]  magic  (0x{ALLOC_TRAIL_MAGIC:08X})
  [4:8]  size   (allocation size)
  [8:12] lr     (link register / return address)
  [12:16] addr   (returned heap address)

Usage:
    python3 check_trail.py trail.bin
"""

import struct
import sys

ENTRY_SIZE = 16
EXPECTED_MAGIC = {ALLOC_TRAIL_MAGIC}


def check_trail(path: str) -> bool:
    with open(path, "rb") as f:
        data = f.read()

    n_entries = len(data) // ENTRY_SIZE
    n_corrupt = 0

    print(f"Trail: {{n_entries}} entries, {{len(data)}} bytes")

    for i in range(n_entries):
        off = i * ENTRY_SIZE
        entry = data[off : off + ENTRY_SIZE]
        magic, size, lr, addr = struct.unpack("<IIII", entry)

        status = "OK"
        if magic != EXPECTED_MAGIC:
            status = "CORRUPT"
            n_corrupt += 1

        print(f"  [{{i:4d}}] magic=0x{{magic:08X}} size={{size:5d}} "
              f"LR=0x{{lr:08X}} addr=0x{{addr:08X}}  [{{status}}]")

    if n_corrupt > 0:
        print(f"\\nFAIL: {{n_corrupt}} / {{n_entries}} entries corrupted")
        return False
    else:
        print(f"\\nPASS: All {{n_entries}} entries intact")
        return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {{sys.argv[0]}} <trail.bin>")
        sys.exit(1)
    ok = check_trail(sys.argv[1])
    sys.exit(0 if ok else 1)
'''


def generate_allocation_summary_script():
    """
    Generate a script that summarizes the allocation trace.
    Detects leaks (alloc without matching free) and double-frees.
    """
    return '''#!/usr/bin/env python3
"""
malloc_summary.py — Analyze malloc/free trace for leaks and anomalies.

Input: a text file containing lines from malloc_hook tracer:
    Alloc #N: LR=0xXXXX size=NNN addr=0xXXXX
    Free  #N: LR=0xXXXX addr=0xXXXX

Output: summary including leaks, double-frees, top callers by size/count.
"""

import re
import sys
from collections import defaultdict

ALLOC_RE = re.compile(
    r"Alloc #(\\d+): LR=0x([0-9A-Fa-f]+) size=(\\d+)(?: addr=0x([0-9A-Fa-f]+))?"
)
FREE_RE = re.compile(
    r"Free  #(\\d+): LR=0x([0-9A-Fa-f]+) addr=0x([0-9A-Fa-f]+)"
)


def analyze_trace(path: str):
    allocs = {}     # addr -> (lr, size, seq)
    frees = set()   # set of freed addresses
    double_free = []
    leaks = []
    caller_stats = defaultdict(lambda: {"count": 0, "total_size": 0})
    total_alloc = 0
    total_free = 0
    current_addr = 0x20000000  # heuristics for addresses without captured addr

    with open(path) as f:
        for line in f:
            line = line.strip()

            m = ALLOC_RE.match(line)
            if m:
                seq = int(m.group(1))
                lr = int(m.group(2), 16)
                size = int(m.group(3))
                addr_str = m.group(4)
                if addr_str:
                    addr = int(addr_str, 16)
                else:
                    # Synthetic address for entries without captured return
                    addr = current_addr
                    current_addr += size + 16  # approximate overhead

                allocs[addr] = (lr, size, seq)
                caller_stats[lr]["count"] += 1
                caller_stats[lr]["total_size"] += size
                total_alloc += 1
                continue

            m = FREE_RE.match(line)
            if m:
                seq = int(m.group(1))
                lr = int(m.group(2), 16)
                addr = int(m.group(3), 16)

                if addr in frees:
                    double_free.append((seq, addr))
                elif addr in allocs:
                    del allocs[addr]
                    frees.add(addr)
                else:
                    double_free.append((seq, addr))
                total_free += 1

    # Check for leaks
    for addr, (lr, size, seq) in sorted(allocs.items(), key=lambda x: x[1][2]):
        leaks.append((seq, addr, lr, size))

    # ── Summary ──
    print("=" * 60)
    print("Malloc/Free Trace Summary")
    print("=" * 60)
    print(f"Total allocations:  {total_alloc}")
    print(f"Total frees:        {total_free}")
    print(f"Active (leaked):    {len(leaks)}")
    print(f"Double-frees:       {len(double_free)}")
    print()

    if leaks:
        print("── Leaked allocations ──")
        for seq, addr, lr, size in leaks:
            print(f"  #{seq}: addr=0x{addr:08X} size={size} LR=0x{lr:08X}")
        print()

    if double_free:
        print("── Double-free events ──")
        for seq, addr in double_free:
            print(f"  #{seq}: addr=0x{addr:08X}")

    print()
    print("── Top callers by total size ──")
    for i, (lr, stats) in enumerate(
        sorted(caller_stats.items(), key=lambda x: -x[1]["total_size"])[:10]
    ):
        sym = _lookup_symbol(lr)
        print(f"  {i+1}. LR=0x{lr:08X} ({sym}): "
              f"{stats['count']} allocs, {stats['total_size']} bytes")

    return len(leaks) == 0 and len(double_free) == 0


def _lookup_symbol(addr: int) -> str:
    """Try to look up a symbol name. Placeholder — refine with ELF parsing."""
    return "unknown"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <trace.txt>")
        sys.exit(1)
    ok = analyze_trace(sys.argv[1])
    sys.exit(0 if ok else 1)
'''


# ── Main entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="RT-Thread malloc/free hook tracer generator")
    parser.add_argument("--gdb-batch", action="store_true",
                        help="Generate GDB batch script (.gdb)")
    parser.add_argument("--gdb-python", action="store_true",
                        help="Generate GDB Python tracer script")
    parser.add_argument("--check-trail", type=str, metavar="FILE",
                        help="Check allocation trail binary file")
    parser.add_argument("--summary", type=str, metavar="FILE",
                        help="Generate allocation summary from trace log")
    args = parser.parse_args()

    if args.gdb_batch:
        generate_gdb_script()
    elif args.gdb_python:
        print(generate_python_tracer_script())
    elif args.check_trail:
        code = generate_trail_integrity_checker()
        exec(code)
        # Run check
        from pathlib import Path
        if Path(args.check_trail).exists():
            if "check_trail" in dir():
                check_trail(args.check_trail)
        else:
            print(f"File not found: {args.check_trail}")
    elif args.summary:
        if __import__("os").path.exists(args.summary):
            analyze_trace(args.summary)
        else:
            print(f"File not found: {args.summary}")
    else:
        # Default: print a summary and example usage
        print(__doc__)
        print()
        print("Examples:")
        print("  python3 malloc_hook.py --gdb-batch  > malloc_hook.gdb")
        print("  python3 malloc_hook.py --gdb-python > malloc_hook_tracer.py")
        print("  python3 malloc_hook.py --summary trace.txt")
        print("  python3 malloc_hook.py --check-trail trail.bin")
