aaxxyy reads from register xx, subregister yy
33xxyy writes to register xx, subregister yy
Registers:
	01 on/off state (also used for keepalive)
		01 is "on", all other values are "off", normalized to 00
	04 dimmer
		(in scene mode) = brightness
		(in temperature mode?) = temperature
	05 mode
		3 bytes
		04 scene
			0100
				rotates between blue, white, and purple
			0300 hue rotate
			0400 sky blue
			0500 reddish orange
			0700 ???
				slightly red-orange with param brightness (5 sec)
				suddenly does hue rotation at max brightness for (3 sec)
			0800 blinking
				blinks with hue rotation
				param = brightness, modular with 3 wraps
				00 = 7
				66 = 0
				d2 = 0
				ff = 45
				some are max brightness regardless of - at 0 brightness, this looks like those colors are blinking with black periods
				writing param restarts animation at red
			0900 candlelight
				flickers between soft red-orange and yellow-orange
				param = brightness
				b5+ = first frame of animation is green? larger seems to add more frames of green/yellow
				fd = no weird colors
			0f00 freezeframe
				stops any animation, param ignored
			0a00 soft pulsate once per hue, rotates
				param = brightness
			1000 quick discrete hue rotation
				param = brightness
				00 = 7
				66 = 0 // 67?
				d2 = 0
				ff = 45
			1500 seizure flash between blue and white
			15ff twinkles between soft pink and blue
			1600 chaser rotating between blue and pur3ple hues
			2300 bright slow pulsating between blue and white, reminds me of ice
			3f00 illumination
				param = brightness
				00 = 7
				66 = 0 // 67?
				d2 = 0
				ff = 45
			4000 chaser with rotating hues
			4100 chaser with red and blue on right, purple on left, toward center
			xxxx
				undefined = some interpretation of the color buffer
		05 microphone mode
			Write 330505 01 to enable
			All subsequent writes use 00 followed by 2 bytes
			From the app, either both bytes are the same value or one is 00
			00xx causes dimming using the full 00-ff range instead of the usual percentage
		15 segments
			acts like a write-only pseudo-register
			subsequent reads of 05 show only 15
			writes start with a command byte:
			01 rrggbb ccccrrggbb sstt
				if cccc == 0:
					first rgb = color
				else:
					first rgb = ffffff
					cccc = BIG-endian color temperature
					rrggbb = equivaleng rgb color
			02 bb sstt
				bb = brightness (%)
				note: per-segment brightness works in addition to 04 dimmer
			sstt = little-endian segment selection bitmap
	06 = "312e30302e3134" = "1.00.14"
		readonly firmware version
	07 "version"?
		02 = "881a353239d3ea86" = MAC + ea86? extra bytes uncertain.
			extra bytes may relate to the product model, but I don't know how H7015 would be encoded here.
		03 = eg "3.01.01", HW version (in app)
		04 = eg "1.00.14", FW version again
		06 = eg "881a353239d3" = MAC address in little-endian order (reversed)
	0e = restart (write to restart, may retain as restart reason?)
		01 = power removed
	0f = "01" unknown, writing valid value is retained while invalid is ignored
		01, 02 are valid, no observable effect
	11 = sleeping settings
		Turning on sets dimmer to initial brightness and decreases over duration
		xx on/off (00=off, 01=on)
		bb initial brightness (%)
		mm duration (minutes, max 240)
		mm duration (minutes, duplicated)
	12 = wake-up settings
		xx on/off (00=off, 01=on)
		bb final brightness (percent)
		hh wake-up hour
		mm wake-up minute
		rr repeat bitmask
			LSB = Monday, days in-order, MSB = immediate?
		dd duration (minutes)
	23 = alarm settings
		4 bytes of 00
		1 byte
		If writing with any of the bits in the first 4 bytes set, write is ignored
		Only the last byte is written
		No observable effect
		Second byte set to 80 when all timers turned on
		Writing:
			xx alarm index (00-03)
			yy alarm state (00=off, 80=on) - more particularly, the MSB other bits unused.
			0000
			?? setting flag? set to 80 sometimes
	40 = "001e" ignores writes
	41 = power-off memory
		"If you enable this function, after the device is powered
		off and on again, it will be restored to the state before the
		power off."
		00 = off
		01 = on
		Retains written value 00:ff
	a3 = ""
		1 byte 00:01, unknown effect
	a5
		00 sub-register seems to be special-use
		Sub-registers 01-ff are composed of 3 units of brightness (%) + rrggbb
		
		00 ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? tttt
			tttt = little-endian color temperature in mode 15 cccc != 0
		Segment block
		01 segments 1-3
		02 segments 4-6
		03 segments 7-9
		04 segments 10-12
		05 segments 13-15
		00
			"ff010101ff01010100006009"
			"ff033c00ff00010000010101"
		01-05
			"45ffa23c45ffa23c45ffa23c"
			"64ffffff64ffffff64ffffff"
		06-0a = "64ffffff64ffffff64ffffff"
		0b
			xx646464yy64646400646464
			xx = scene id byte 1
			yy = scene id byte 2 (unused?)
			"0f4545450045454500454545" // doesn't follow pattern
			"096464640064646400646464"
		0c = "ff454545ff646464ff646464"
			"006464640064646400646464"
		0d = "006464640064640f0000ffff"
			00646464006464xx00010101
			xx = scene id
		0e = "000101010101004600ff"
		0f = "46ff000009ff0000ff010101"
		10 = "00ffff8b00ff0000ff010101"
		11 = "7f01010100010101ff010101"
		12 = "ff0101010001010100010101"
		13 = "ff0101010001010100010101"
		14 = "00010101ff01010100010101"
		15 = "ff010101ff01010615218b010101"
		16 = "00010101ff01010100010101"
		17-ff = "000101010001010100010101"
		
		xx010101 is used as an "undefined" color and fills the unused subregisters
	ee = ""
		rff="" => read pauses animation
		write no effect, always ""
	ef = "000101"
		always "000101" no matter what is written
	ff = ""
		01 = resume
		02 = pause, if scene set the animation plays as fast as possible (seems to crash, doesn't respond to commands )
		03 = pulses red
		05 = set to this when animation is playing? writing this pauses animation
		xx = pause animation

a3 multi-packet write command
- All a3 packets are a3xx, where xx is the packet number.
- The start packet is a300 01 pp 02 ...data where pp is the number of packets including the start and end packets.
- The last packet has packet number ff and also holds data.
- All packets are XOR checksum and zero-padded like always.

a5 audio mode
- Unknown specifications

## Sources
- [Homeassistant](https://github.com/Beshelmek/govee_ble_lights)
- [Reverse engineer](https://github.com/BeauJBurroughs/Govee-H6127-Reverse-Engineering)

From the hass repo and cross-referencing with my reverse engineering, the H7015 seems to be most similar to H7021

The BLE device name is of the form Govee_MODEL_MAC2, where MODEL is the model name (eg H7015) and MAC2 is the last 2 bytes of the MAC address.