#!/usr/bin/env bash
# SPI4 GPIO Register Diagnostic — CUAV V5
# Usage: ./check_spi4_gpio.sh [gdb|telnet]
# Checks PE12/PE13/PE14 AF5 configuration via GDB or OpenOCD telnet

METHOD="${1:-gdb}"

spi4_pins_ok() {
    local moder="$1" afrh="$2"
    # MODER: PE12=bits25:24=10(AF), PE13=bits27:26=10(AF), PE14=bits29:28=10(AF)
    local moder_pe12=$(( (moder >> 24) & 3 ))
    local moder_pe13=$(( (moder >> 26) & 3 ))
    local moder_pe14=$(( (moder >> 28) & 3 ))
    # AFRH: PE12=bits19:16=0101(AF5), PE13=bits23:20=0101(AF5), PE14=bits27:24=0101(AF5)
    local afrh_pe12=$(( (afrh >> 16) & 0xF ))
    local afrh_pe13=$(( (afrh >> 20) & 0xF ))
    local afrh_pe14=$(( (afrh >> 24) & 0xF ))

    echo "  PE12 (SCK): MODER=$moder_pe12 (want 2=AF) AFR=$afrh_pe12 (want 5)"
    echo "  PE13 (MISO): MODER=$moder_pe13 (want 2=AF) AFR=$afrh_pe13 (want 5)"
    echo "  PE14 (MOSI): MODER=$moder_pe14 (want 2=AF) AFR=$afrh_pe14 (want 5)"

    if [ "$moder_pe12" = "2" ] && [ "$moder_pe13" = "2" ] && [ "$moder_pe14" = "2" ] \
       && [ "$afrh_pe12" = "5" ] && [ "$afrh_pe13" = "5" ] && [ "$afrh_pe14" = "5" ]; then
        return 0
    fi
    return 1
}

if [ "$METHOD" = "gdb" ]; then
    echo "=== SPI4 GPIO Check (via GDB) ==="
    output=$(arm-none-eabi-gdb -batch \
        -ex "target extended-remote :3333" \
        -ex "monitor halt" \
        -ex "monitor mdw 0x40021000 8" \
        -ex "monitor mdw 0x40021020 4" \
        -ex "monitor resume" 2>&1 | grep "0x4002")

    moder=$(echo "$output" | grep "0x40021000:" | awk '{print $2}')
    afrh=$(echo "$output" | grep "0x40021020:" | awk '{print $5}')

elif [ "$METHOD" = "telnet" ]; then
    echo "=== SPI4 GPIO Check (via OpenOCD telnet) ==="
    moder=$(echo "mdw 0x40021000 1" | nc -q1 localhost 4444 2>/dev/null | grep "0x40021000:" | awk '{print $2}')
    afrh=$(echo "mdw 0x40021024 1" | nc -q1 localhost 4444 2>/dev/null | grep "0x40021024:" | awk '{print $2}')
else
    echo "Usage: $0 [gdb|telnet]"
    exit 1
fi

echo "GPIOE MODER = 0x${moder}"
echo "GPIOE AFRH  = 0x${afrh}"

# Parse as hex (handle potential leading zeros issues)
moder_dec=$((16#${moder}))
afrh_dec=$((16#${afrh}))

if spi4_pins_ok $moder_dec $afrh_dec; then
    echo ""
    echo ">>> SPI4 GPIO OK <<<"
    exit 0
else
    echo ""
    echo "!!! SPI4 GPIO MISCONFIGURED !!!"
    echo "Expected: PE12/PE13/PE14 = AF5"
    echo "Check CubeMX MSP init, rt_board_init.c, and hwdef.dat"
    exit 1
fi
