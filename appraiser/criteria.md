# Appraiser criteria reference

Full lookup tables for the appraiser. The agent reads `agent_brief.md`
on every batch (small, always-needed). It reads **this** file only when
it hits a borderline call: an unfamiliar brand, an ambiguous category, a
condition modifier it's unsure about. Most listings won't need it.

---

## Component taxonomy (canonical strings — use exactly)

`gpu, cpu, motherboard, ram, psu, ssd, hdd, laptop_battery, laptop_screen,`
`laptop_keyboard, stepper_motor, servo_motor, brushless_motor, dc_motor,`
`lithium_battery_pack, battery_18650_cell, single_board_computer,`
`microcontroller, esc, vesc, lidar, camera_module, kinect_sensor,`
`lcd_panel, ribbon_cable, linear_rail, leadscrew, ball_bearing, hotend,`
`extruder, build_plate, power_brick, transformer, solenoid, relay,`
`soldering_station, multimeter, oscilloscope, bench_psu, tool_battery,`
`drill, saw_blade, hub_motor, ebike_battery, ebike_controller,`
`router_board, webcam, speaker_driver, encoder, ir_sensor, cliff_sensor,`
`imu, other`

---

## Categories of interest (RG = really-good tier)

| Category | Why | RG |
|---|---|---|
| any printer | steppers, linear rails, belts, gears, regulated PSU | – |
| laser printer | + bigger PSU + extra motors | RG |
| flatbed scanner | stepper + smooth linear rail | – |
| treadmill | big DC motor 1+ hp, controller, frame | RG |
| ATX / desktop PSU | bench-style 12V/5V/3.3V supply | – |
| bench PSU | variable lab supply | RG |
| oscilloscope | lab measurement | RG |
| multimeter | workshop staple | – |
| soldering iron / station | workshop staple | – |
| Arduino / ESP32 / generic Pi | cheap compute | – |
| Raspberry Pi 4 / 5 | better SBC | RG |
| stepper / NEMA17 | motion-control actuator | – |
| brushless motor | RC / robotics actuator | – |
| ESC | brushless motor driver | – |
| servo (hobby) | robotics actuator | – |
| RC car | brushless + ESC + battery + chassis | – |
| drone / quadcopter | brushless + ESC + LiPo + IMU + camera | – |
| 3D printer (broken OK) | steppers, hotend, controller, rails | RG |
| hoverboard | 2 hub motors + Li-ion + IMU | RG |
| electric scooter / e-bike / e-skateboard | hub motor + controller + battery | RG |
| VESC | premium brushless controller | RG |
| robot vacuum | encoders, IR/cliff sensors, lidar on premium | RG |
| Kinect | depth + RGB camera + IR projector | RG |
| webcam | UVC USB camera | – |
| network router | OpenWrt-able SBC | – |
| cordless drill / power tool | brushed motor + planetary gearbox | – |
| electric wheelchair | high-torque motors + gearbox + joystick | RG |
| CNC / lathe / mill | steppers, drivers, linear rails | RG |
| VCR / tape deck | precision motors, gears, belts | – |
| old laptop | Linux compute + battery + screen | – |
| electronics lot / parts lot | mixed bag, variable | RG |

---

## Premium brands (rough multipliers, cap ×2.0)

Apply only if the comp/anchor doesn't already reflect the brand.

**Test gear:** Fluke ×2.0 · Keysight ×2.0 · Tektronix ×1.8 · Rigol ×1.7
· Siglent ×1.5

**Soldering:** Weller ×1.5 · Hakko ×1.6 · Metcal ×1.8

**3D printing:** Bambu ×2.0 · Prusa ×2.0 · Anycubic ×1.3 · Creality
×1.3 · Ender ×1.3

**Robotics / vacuums:** iRobot ×1.4 · Roomba ×1.4 · Neato ×1.3

**E-mobility:** Boosted ×2.0 · Evolve ×1.7 · Meepo ×1.4

**Drones / RC:** DJI ×1.7 · Parrot ×1.4 · Traxxas ×1.5 · Arrma ×1.4

**Mobility:** Permobil ×1.6 · Quickie ×1.4

**CNC:** Haas ×1.8 · Tormach ×1.6 · Shapeoko ×1.4 · Carbide3D ×1.4

**Power tools:** Milwaukee ×1.4 · DeWalt ×1.4 · Makita ×1.4 · Bosch ×1.3

**Compute:** ThinkPad ×1.3 · Raspberry Pi ×1.2

**Driver chips:** Trinamic ×1.5 · TI ×1.3

---

## Condition multipliers

| Condition | Multiplier |
|---|---|
| new | ×1.25 |
| like new | ×1.20 |
| excellent | ×1.15 |
| good | ×1.00 |
| fair | ×0.85 |
| salvage / parts only / for parts | ×0.50 |

If no structured condition attribute, infer from body keywords:
- "working / tested / like new" → ×1.15
- "broken / for parts" → ×0.70
- "missing / no power / untested" → ×0.50

---

## Excluded — never appraise

Bicycles · office chairs · CRT TVs · loose used lithium batteries.

---

## Killed to $0 (skip with skip_reason)

**Buyer-side titles:** "wanted", "ISO", "WTB", "for trade", "looking
for", "will pay cash for".

**Accessory-only titles:** "ink cartridge", "filament", "laptop bag",
"drone props", "ebike battery only", "tire only".

(Most of these are caught by `prepare.py`'s prefilter before you see
them — apply the gate yourself as a safety check.)

---

## Quantity

`lot of N` or `N units` → multiply the line by `min(N, 3)`.

---

## Buy decision (FYI — applied by aggregator, not you)

- Free → BUY if in-zone (D requires really-good)
- ≤ $20 → BUY if salvage ≥ 2× ask
- $21–$30 → BUY if salvage ≥ 3× ask AND really-good
- > $30 → never (these are dropped at prefilter, you won't see them)

You produce honest salvage ranges. The aggregator decides BUY/MAYBE/SKIP.
