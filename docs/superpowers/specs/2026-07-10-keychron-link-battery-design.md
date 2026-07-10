# Stock Keychron Link Battery Support Design

## Objective

Add native Linux battery reporting for a stock-firmware Keychron keyboard connected through a Keychron Link wireless receiver. The first acceptance target is a Keychron Link USB-A receiver (`3434:d030`, firmware `d.3.0`) paired with a stock Keychron V6 Max (`3434:0960`). The design must leave room for the USB-C Link (`3434:d031`), additional receiver slots, and custom-firmware protocols without weakening the stock-firmware requirement.

Battery data will ultimately be exposed through Linux `power_supply`, and therefore UPower, without requiring keyboard or receiver firmware changes.

## Current Evidence

The existing module supports a Keychron M5 mouse and Ultra-Link receiver. It assumes interface 4, 64-byte reports, request report `0xB3`, response report `0xB4`, command `0x06`, and battery byte 20. It also uses a single global battery owner and always publishes `keychron_mouse`.

The connected Keychron Link has four HID interfaces. Its relevant collections are:

- Usage page `0x8c`, usage `0x01`, for receiver detection and status.
- Usage page `0xff60`, usage `0x61`, with 32-byte input and output reports for receiver commands and potential keyboard tunneling.

Confirmed receiver commands on the raw channel are:

- `0xb1`: receiver protocol version. The connected receiver reports version 1 and no feature flags.
- `0xb2`: paired-device state. Slot 0 contains connected device `3434:0960`.
- `0xb3`: receiver firmware version. The connected receiver reports `d.3.0 Jun 7 2024 12:32:18`.

Passive reconnect capture produces connection-state reports (`0xe2` and `0xb2`) but no visible battery percentage. Both unencoded stock-style VIA commands and the custom Keychron Vial XOR transport time out on this receiver/keyboard pair.

The stock V6 Max firmware calculates battery percentage locally and sends it to the LKBT51 wireless module with `LKBT51_CMD_UPDATE_BAT_LVL` (`0x32`) when the wireless connection is established. Stock firmware does not expose a host raw-HID battery query. This suggests the receiver or wireless module may cache battery state, but the host-side retrieval mechanism is not yet known.

## Chosen Approach

Use a temporary userspace protocol probe to establish the stock receiver protocol before changing the kernel driver. The probe is an investigation tool, not a permanent daemon. Kernel implementation begins only after a reproducible stock-firmware battery field or query has been demonstrated.

This approach is preferred over kernel-first experimentation because userspace provides safer iteration, complete packet transcripts, and clearer handling of USB stalls and timeouts. A permanent userspace service is not the target because the project should retain native `power_supply` integration.

## Investigation Architecture

The probe has four isolated components:

1. **Device discovery** locates supported Keychron receivers and selects channels by HID usage page, usage, and report descriptor. It never relies on transient `/dev/hidrawN` numbering.
2. **Transport** performs bounded HID exchanges, records timestamps and complete request/response packets, and reports retries, timeouts, and USB errors precisely.
3. **Protocol decoders** keep Link receiver, stock keyboard, custom-firmware keyboard, and existing mouse formats separate. Each decoder converts only validated packets into normalized observations.
4. **Experiments** describe one reproducible scenario and one hypothesis, such as baseline capture, reconnect capture, or one candidate read command.

### Investigation Safety Rules

- Start with the confirmed read-only receiver commands `0xb1`, `0xb2`, and `0xb3`.
- Derive candidate battery commands from firmware source, current Keychron Launcher code, receiver firmware analysis, or captured traffic.
- Do not brute-force command or report-ID ranges.
- Block reset, pairing, configuration writes, bootloader entry, and DFU commands.
- Change one protocol variable per experiment.
- Preserve raw captures even when a hypothesis fails.
- Stop after three rejected hypotheses and review the transport and architecture assumptions before continuing.
- Do not place a speculative command in the kernel module.

Fn+B provides approximate ground truth in 10% buckets. A candidate field is accepted only when it produces plausible `0-100` values repeatedly, tracks at least one later battery change, and does not interrupt keyboard input. If receiver firmware `d.3.0` provides only a value cached at reconnect, that freshness behavior must be measured and documented.

If no retrievable percentage exists in stock receiver firmware, the investigation will document that hardware boundary. The project will not synthesize a percentage from connection state, elapsed time, or an unverified field.

### Investigation Deliverables

- Raw, timestamped HID captures for each experiment.
- Decoded annotations that cite the corresponding raw bytes.
- A protocol note defining confirmed requests, responses, endianness, timing, and freshness.
- Repeatable fixture tests for confirmed decoders and rejected malformed packets.
- A clear go/no-go decision for the kernel implementation.

## Production Driver Architecture

After the protocol gate passes, the module will normalize all supported devices through protocol operations rather than a single mouse-specific implementation.

```text
HID receiver
    |
receiver transport
    |
    +-- stock Link keyboard protocol --+
    +-- custom Link protocol ----------+--> normalized battery state
    +-- existing M5 mouse protocol ----+             |
                                                   power_supply
                                                        |
                                                      UPower
```

The protocol boundary owns device matching, request construction, response validation, and extraction of normalized capacity, presence, charging status when known, model identity, and receiver slot.

### Device and Interface Handling

- Detect the vendor channel from HID usage and report structure instead of a fixed interface number.
- Preserve normal keyboard, mouse, consumer-control, and joystick behavior on unrelated interfaces.
- Maintain state per physical receiver instead of using the current global singleton.
- Represent receiver slots independently even though the initial V6 Max target occupies one slot.
- Use stable, unique power-supply names based on receiver identity and slot.
- Use the paired VID/PID for a known model name when available and a generic Keychron keyboard name otherwise.

### Transactions and Coexistence

- Serialize driver-originated transactions per receiver.
- Match responses by protocol, command, expected length, and slot before changing battery state.
- Never consume or reinterpret unmatched raw reports.
- Handle unsolicited connection notifications separately from command responses.
- Keep hidraw available so Keychron Launcher, Vial, and diagnostic tools can use the receiver.
- Test concurrent configuration-tool access explicitly. Low polling frequency alone is not considered sufficient proof of coexistence.

### Power-Supply Lifecycle

- Register a battery only after the first valid capacity reading.
- Publish capacity only in the inclusive range `0-100`.
- Retain the last valid capacity across a temporary sleep or missed query.
- Set `PRESENT=0` after a confirmed receiver-slot disconnect.
- Restore presence and refresh capacity automatically after reconnect or resume.
- Call `power_supply_changed()` only when a published property changes.
- Do not create a power-supply device for unsupported firmware or before a valid reading.

The existing M5 behavior remains a separate protocol adapter. Refactoring is limited to what is needed to remove mouse-only assumptions and safely host multiple protocol implementations.

## Failure Handling

- Reject truncated packets, unexpected commands, wrong slots, invalid report formats, and capacities above 100.
- Use bounded request retries with explicit response timeouts.
- Poll sleeping or temporarily unavailable devices at a slower retry interval; do not busy-loop.
- Leave the last valid capacity unchanged after malformed traffic or a transient timeout.
- Log unsupported receiver firmware once with receiver version information and without creating a fake battery.
- Cancel delayed work and outstanding USB/HID transfers before freeing device state.
- Make removal, suspend, resume, and failed-probe cleanup safe when the battery has not yet been registered.

## Verification Strategy

### Protocol and Parser Tests

- Decode every captured stock receiver packet fixture.
- Retain fixtures for the existing M5 protocol.
- Add custom-firmware fixtures only when that protocol is implemented.
- Test truncated packets, incorrect commands, invalid percentages, wrong slots, unsolicited events, and timeout paths.
- Verify request bytes exactly so a protocol refactor cannot silently change device traffic.

### Kernel Checks

- Compile against the running kernel headers.
- Compile against at least one supported LTS kernel header set.
- Exercise probe failures and cleanup through fault-oriented tests where practical.
- Run repeated module load/unload and receiver unplug/replug cycles while checking kernel logs for warnings, leaks, and use-after-free reports.

### Live Acceptance Matrix

- Receiver already connected when the module loads.
- Keyboard asleep when the module loads, followed by wake.
- Keyboard switched off and back to 2.4 GHz.
- Receiver unplugged and reinserted.
- System suspend and resume.
- Keyboard charging and discharging when the protocol exposes charging state.
- Capacity changes after discharge or reconnect and reaches UPower.
- Keychron Launcher or Vial open while battery polling is enabled.
- Continuous normal typing during polling, reconnect, and tool coexistence tests.

## Acceptance Criteria

Stock V6 Max support is complete only when all of the following are true:

- No keyboard or receiver firmware change is required.
- A battery device appears through both sysfs `power_supply` and UPower.
- Reported capacity agrees with the keyboard's Fn+B 10% bucket.
- At least one later battery change is observed and propagated.
- Sleep/wake, disconnect/reconnect, receiver replug, and suspend/resume recover automatically.
- Keyboard input remains reliable and configuration tools remain usable.
- Invalid or unrelated HID traffic cannot update the published capacity.

Support for custom firmware is intentionally deferred until the stock path is proven. It will be added as a separate protocol adapter and will not replace or weaken stock-firmware support.

## Out of Scope

- Flashing or modifying keyboard or receiver firmware.
- Estimating capacity from usage time or connection state.
- Permanent userspace battery reporting.
- Unrelated cleanup or redesign of the Keychron configuration applications.
- Claiming support for untested Keychron models solely because they share the vendor ID.
