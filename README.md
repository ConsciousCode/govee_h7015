# Govee H7015
These are BLE-only RGB string lights with 15 discrete individually addressable bulbs/segments. They are controlled by a Govee app and have no IR or RF remote. Since I'm not a masochist and don't want to call a REST API to control a local bluetooth device, I reverse engineered the protocol and wrote a python library to control them.

The bulk of the insights I've gathered are in [research](./research.md).

To look at the raw notes and snoop logs, see [raw](./raw.log). This is not intended to be particularly readable, mostly just documentation of my process.

## Warning
In poking the various registers, I've managed to softlock my device multiple times which required removing the power to get out. Nothing so far has bricked it, but I can't make any guarantees. You use raw commands at your own risk.

## Usage
### MQTT
```bash
python mqtt.py
```
Starts an MQTT client that listens for commands to control the lights. I've made no effort to make this configurable so you'll need to change the host yourself. The command API is modeled after tasmota and is as follows:
- `cmnd/govee/...` - Run a given command with the arguments in the message. Usually space-separated values.
- `stat/govee/RESULT` - The result of the last command. Always JSON.
  - On error, returns `{"ERROR": "error message"}`. Some commands return additional context or incomplete results.
  - On success, returns `{Command: result}`

#### Commands
- `power` - Get or set the power state (register `01`).
  - Accepts `1|0|on|off|true|false|toggle`, case insensitive.
- `dimmer` - Get or set the global dimmer level (register `04`).
  - Accepts floats or percentages.
- `mode` - Readonly the current mode (register `05` + some others).
  - `{"name": "color", "brightness": percent, "color": hex}` - All segments are the same color and brightness.
  - `{"mode": "segment", "segments": [{"color": hex, "brightness": percent}]}` - Each segment has its own color and brightness.
  - `{"mode": "scene", "name": scene, "code": int}` - The device is in a scene mode.
- `version` - Readonly device version information.
  - `version[0]` - Get all device information as a tuple.
  - `version1` - Get the device version (HW version, register `06`).
  - `version2` - Get the device HW version (register `0703`).
  - `version3` - Get the device FW version (register `0704`).
- `mac` - Get the device MAC address (register `0705`).
- `restart` - Get the restart reason or restart the device (register `0e`).
- `status` - Get all device information.
- `scene` - Get or set the current scene (`a3` command with param data + `w0504xxxx`).
  - This can be `Category - Scene`, `Scene`, or a (possibly hex with 0x) id. Case and whitespace insensitive.
- `scenes` - Readonly list of scenes and their ids, `{Category: {Scene: ID|{"code": code, "diy": diyCode}}}`.
- `brightness` - Get or set the brightness level of all segments if it's the same, else `null`.
- `color` - Get or set the color of all segments if it's the same, else `null`.
  - Note that if their brightness isn't the same, the API will still say it's in segment mode.
- `peek` - Read a register or range of registers (`33`).
  - Accepts a comma-separated list of hex registers or ranges.
- `multi` - Initiate a multi-packet write command (`a5`) which writes scene parameters.
  - Accepts *hex* data, not base64.

#### Dangerous commands
**USE AT YOUR OWN RISK**
- `poke` - Write a register (`33`).
  - Accepts a space-separated hex register address and hex data to write.
- `raw` - Send raw data to the device.
- `asm` - Send commands from the command language to the device.

### Raw interface
```bash
python govee.py
```
Scans for compatible Govee devices and enters a command prompt tailored for reverse-engineering. A custom command language is used to send commands to the device.

#### Command language
Very loose command language with no validation. Numbers are always hex and keywords get replaced with their byte equivalents.
- `r` read (`aa`)
- `w` write (33)
- prefix / change / suffix... eg `r05/aa,bb,cc/01` = queue commands `aa05aa01` `aa05bb01` `aa05cc01`
- `xx-yy` = range of values
    `w0504/00-ff` = send 255 write commands to `0504` with successive values `00-ff`
- `scene` = `0504` eg `w scene 10` = `33050410`
- `param` = `04`
- `restart` = `w 0e 00` or `restart xx` = `w 0e xx`
- Spaces are mostly ignored except for keyword word boundaries.

### Consolidate
```bash
python consolidate.py
```
Consolidates the raw data into a more readable format. This is a one-time operation and is not intended to be run regularly.

## Todo 
- [x] Map registers (partially complete)
- [x] MQTT client to act as a persistent bridge.
- [x] Set the color and brightness of segment subsets.
- [x] Get current colors and brightness of segments from device.
- [x] Set a parameterized scene.
- [x] Clean up API dump data ([jsons](./jsons/) -> [scenes](./scenes/)).
- [x] Set scenes by name.
- [x] Use snoop log to get [DUNE collaboration scenes](./collaboration.json).
- [ ] Implement color temperature feature.
- [ ] Reverse engineer scene param encoding (try DIY scenes in the app?).
- [ ] Finish mapping where scene param data actually goes.
- [ ] Able to extract all scene data from the device.
- [ ] What do sleep, wake, and timer actually do?
- [ ] Better understand color buffer in register `a5xx`.
- [ ] Distinguish `w mode 05` "mic mode" and `a5` "audio mode".
  - [ ] Reverse engineer them too.

### Low priority questions
- [ ] What are the extra services and characteristics?
- [ ] Unknown registers: `0f`, `11`, `12`, `23`, `40`, `ee`, `ef`, `ff`.
- [ ] What are the extra `ea86` bytes appended to the little-endian MAC in register `0702`?
- [ ] Why is the HW version repeated in `06` and `0703`?

## Credits
- [govee_ble_lights (Homeassistant)](https://github.com/Beshelmek/govee_ble_lights/)
  - API dumps for BLE govee lights.
  - Practical implementations of some parts of the API.
- [Govee-Reverse-Engineering](https://github.com/egold555/Govee-Reverse-Engineering/)
  - Reverse-engineering of incompatible APIs which still helped inform my own research.