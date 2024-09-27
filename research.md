From the hass repo and cross-referencing with my reverse engineering, the H7015 seems to be most similar to H7021

The BLE device name is of the form Govee_MODEL_MAC2, where MODEL is the model name (eg H7015) and MAC2 is the last 2 bytes of the MAC address.

The first byte of any message sent to the SEND characteristic is the main command followed by its payload. All messages sent or received are zero-padded to 19 bytes followed by a single XOR checksum byte without exception.

The Govee firmware uses a register-based design.

`aa` reads from a register, which may include sub-register addressing. If the given register isn't a sub-register, all remaining bytes are ignored. For *valid* register addresses, it responds on the RECV characteristic with an ACK (`aa` + register address) followed by the contents of the (sub)register.

`33` writes to a register. Some registers are specially mapped to have "pseudo-registers" which can't be addressed by `aa` but have special handling when written. Some are also normalized to a range, and successful writes are not a guarantee that the firmware recognizes those values.[^1] For valid register *addresses* (regardless of validation), it responds on the RECV characteristic with an ACK (`33` + register address). The contents are not included and must be read if needed.

`a3` is a multi-packet write command used to send scene parameters. All packets are prefixed by `a3` and their packet number. The first packet has the prefix followed by `01 pp 02` where `pp` is the number of packets including the start and end packets. The rest of the first packet is the first 14 bytes of data. Subsequent packets only have the prefix and 17 bytes of data. The last packet has a packet number of `ff` and the remaining data, zero-padded. If successful, the firmware will respond with an ACK of `a302`. Some edge cases:
- The data is 14  byte or less, unknown. It's possible scene param encoding ensures this isn't possible.
- `(len(data) - 14) % 17 == 0`, unknown. Does the final packet have the last 17 bytes, or is it entirely 0s? It's possible the firmware doesn't care either way.

`a5` audio mode: unknown specification. TODO.

[^1]: For example, the power register (`01`) is normalized to `00` for off and `01` for on. Writing `02` will turn it off as if you had written `00`. Values outside this range are not preserved.

## Registers
This is the map of registers I've found so far. The register address is in hex, and the contents are in hex unless otherwise noted. Some of this is from the snoop log while others are from fuzzing commands like `r/00-ff` to see what responds.
- `01` on/off state (also used for keepalive)
  - `01` is "on", all other values are "off", normalized to `00`
- `04` dimmer (in percent)
- `05` mode
	- `04` scene
    - All of these were found through poking registers, they may be invalid states of the firmware.
		- `0100`
			- rotates between blue, white, and purple
		- `0300` hue rotate
		- `0400` sky blue
		- `0500` reddish orange
		- `0700` ???
			- slightly red-orange with param brightness (5 sec)
			- suddenly does hue rotation at max brightness for (3 sec)
		- `0800` blinking
			- blinks with hue rotation
		- `0900` candlelight
			- flickers between soft red-orange and yellow-orange
		- `0f00` freezeframe
			- stops any animation, param ignored
		- `0a00` soft pulsate once per hue, rotates
			- param = brightness
		- `1000` quick discrete hue rotation
		- `1500` seizure flash between blue and white
		- `15ff` twinkles between soft pink and blue
		- `1600` chaser rotating between blue and pur3ple hues
		- `2300` bright slow pulsating between blue and white, reminds me of ice
		- `3f00` illumination
		- `4000` chaser with rotating hues
		- `4100` chaser with red and blue on right, purple on left, toward center
		- `xxxx`
			- Whatever is currently in the color buffer. This allows the addition of extra custom scenes by loading their data via scene params and referring to them in-app by the otherwise undefined scene id.
	- `05` "mic mode"
		- Write `330505 01` to enable
		- All subsequent writes use `00` followed by 2 bytes
		- From the app, either both bytes are the same value or one is `00`
		- `00xx` causes dimming using the full `00-ff` range instead of the usual percentage
	- `15` segments
		- acts like a write-only pseudo-register
		- subsequent reads of `05` show only `15`
		- writes start with a command byte:
		- `01 rrggbb ccccrrggbb sstt`
			- `if cccc == 0`:
				- first rgb = color
			- `else`:
				- first rgb = `ffffff`
				- `cccc` = BIG-endian color temperature, 2000-8800 K
				- `rrggbb` = equivalent rgb color
		- `02 bb sstt`
			- `bb` = brightness (%)
			- note: per-segment brightness works in addition to `04` dimmer
		- `sstt` = little-endian segment selection bitmap
- `06` = "312e30302e3134" = "1.00.14"
	- readonly firmware version
- `07` "version"?
	- `02` = "881a353239d3ea86" = MAC + `ea86`? extra bytes uncertain.
		- extra bytes may relate to the product model, but I don't know how H7015 would be encoded here.
	- `03` = eg "3.01.01", HW version (in app)
	- `04` = eg "1.00.14", FW version again
	- `06` = eg "881a353239d3" = MAC address in little-endian order (reversed)
- `0e` = restart (write to restart, may retain as restart reason?)
	- `01` = power removed
- `0f` = "01" unknown, writing valid value is retained while invalid is ignored
	- `01`, `02` are valid, no observable effect
- `11` = sleeping settings
	- Turning on sets dimmer to initial brightness and decreases over duration
	- `xx` on/off (00=off, 01=on)
	- `bb` initial brightness (%)
	- `mm` duration (minutes, max 240)
	- `mm` duration (minutes, duplicated?)
- `12` = wake-up settings
	- `xx` on/off (`00`=off, `01`=on)
	- `bb` final brightness (percent)
	- `hh` wake-up hour
	- `mm` wake-up minute
	- `rr` repeat bitmask
		- LSB = Monday, days in-order, MSB = immediate?
	- `dd` duration (minutes)
- `23` = alarm settings
	- `xx` alarm index (`00-03`)
	- `yy` alarm state (`00`=off, `80`=on) - more particularly, the MSB other bits unused.
	- `0000`
	- `??` setting flag? set to `80` sometimes
- `40` = "001e" ignores writes
- `41` = power-off memory
	- "If you enable this function, after the device is powered off and on again, it will be restored to the state before the power off."
	- `00` = off
	- `01` = on
	- Retains written value `00:ff`
- `a3` = ""
	- 1 byte `00:01`, unknown effect
- `a5` color buffer?
  - All sub-registers are 12 bytes long, almost always 3 units of 1 brightness byte with 3 rgb bytes.
  - `xx010101` is used as an "undefined" color and fills the unused subregisters
  - The buffer isn't typically cleared when the mode changes, relevant bytes are just overwritten.
	- `00` sub-register seems to be special-use
	- Sub-registers `01-ff` are composed of 3 units of brightness (%) + rrggbb
	- `00 ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? tttt`
		- `tttt` = little-endian color temperature in mode `15` `cccc != 0`
	- Segment block
    - `01` segments 1-3
    - `02` segments 4-6
    - `03` segments 7-9
    - `04` segments 10-12
    - `05` segments 13-15
  - scene param colors?
    - `a531+4` up to `a59b+0` seem to be color data
  - scene param brightness?
    - `a59b+4` up to (unknown) seem to be brightness data, where every 16 bytes is a single brightness byte.
- `ee` = ""
	- `r ff`="" => read (subregister?) "pauses" animation, suggests FW bug.
	- write no effect, always ""
- `ef` = "000101"
	- always "000101" no matter what is written
- `ff` = ""
  - **WARNING**: Poking around in this register has caused my device to softlock and require a power cycle. It's possible it was never intended to be used. These notes are *incomplete*.
	- `01` = resume
	- `02` = pause, if scene set the animation plays as fast as possible (seems to crash, doesn't respond to commands )
	- `03` = pulses red
	- `05` = set to this when animation is playing? writing this pauses animation
	- `xx` = pause animation

## Sources
- [Homeassistant](https://github.com/Beshelmek/govee_ble_lights)
- [Reverse engineer](https://github.com/BeauJBurroughs/Govee-H6127-Reverse-Engineering)