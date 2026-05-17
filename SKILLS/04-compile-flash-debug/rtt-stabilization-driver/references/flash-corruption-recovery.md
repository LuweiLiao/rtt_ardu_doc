# Flash Corruption Recovery (2026-05-11)

## Phenomenon

During OpenOCD flash write, the algorithm times out:
```
Error: timeout waiting for algorithm
Error: error writing to flash at address 0x08008000 at offset 0x00000000
```

When this happens, the resulting flash content is **partially corrupted** — some sectors may have been written, some not. The ELF was verified before write but the flash is now in an inconsistent state.

## Root Cause

OpenOCD's flash write algorithm needs to run on the MCU. If the MCU is in an unknown state (e.g. from a previous IWDG reset, or bootloader was running), the algorithm may not get enough CPU cycles to complete. The "timeout" means the algorithm started but didn't finish within OpenOCD's wall-clock timeout.

## Detection

If you burn a corrupted firmware, the symptom is **complete silence** — no CDC enumeration, no MAVLink, no OpenOCD-visible code execution. Reading the first vector table entry:

```bash
echo "mdw 0x08008000 4" | nc -q 1 localhost 4444
# Before corruption (good): 0x20005424 0x080EE921 0x080EE927 0x0800887D
# After corruption (bad):   0xFFFFFFFF 0xFFFFFFFF ... (blank)
```

Or more subtly — the binary loads but the first instruction jumps to garbage, producing `hal_run_called=0xdeadbeef` instead of incrementing.

## Recovery Procedure (Verified)

When you hit the timeout:

```bash
# 1. Halt
echo "halt" | nc -q 1 localhost 4444

# 2. Erase all sectors used by app (1-11 for CUAV V5)
echo "flash erase_sector 0 1 11" | nc -q 1 localhost 4444

# 3. MUST reset-halt between erase and write!
#    This clears any stuck flash controller state
echo "reset halt" | nc -q 1 localhost 4444

# 4. Wait for reset to complete
sleep 1

# 5. Write (this time it should succeed because reset cleared the state)
echo "flash write_image /absolute/path/to/rtthread.bin 0x08008000" | nc -q 10 localhost 4444

# 6. Verify
echo "verify_image /absolute/path/to/rtthread.bin 0x08008000" | nc -q 5 localhost 4444
# Expected: Verified OK

# 7. Reset and run
echo "reset run" | nc -q 1 localhost 4444
```

## Prevention

Always use `reset halt` before `flash write_image`. The sequence `erase → reset halt → write_image` is more reliable than `erase → write_image`.

Do NOT use `program` command for this reason — it internally does erase+write without the reset-halt in between.

## GDB Equivalent

```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor reset halt" \
  -ex "monitor flash write_image /absolute/path/to/rtthread.bin 0x08008000" \
  -ex "monitor verify_image /absolute/path/to/rtthread.bin 0x08008000" \
  -ex "monitor reset run" \
  build/rtt_deploy/cuav_v5/rt-thread.elf
```
