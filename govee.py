import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import (
    Awaitable,
    Callable,
    Coroutine,
    Final,
    Generator,
    Iterable,
    Literal,
    NamedTuple,
    Optional,
    overload,
)
from bleak import BleakScanner, BleakClient
from aioconsole import ainput
import re
import traceback as tb
import logging
import time
import json
import binascii
from functools import cached_property

from consolidate import ConsolidateCategories

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)
_LOGGER.addHandler(logging.StreamHandler())

GOVEE_RE: Final = re.compile(r"Govee_(H[\da-f]{4})_[\da-f]{4}", re.I)
RANGE_RE: Final = re.compile(r"([\da-f]+)(?:\s*-\s*([\da-f]+))?")

SERVICE_REGISTERS: Final = "00010203-0405-0607-0809-0a0b0c0d1910"
CHAR_RECV: Final = "00010203-0405-0607-0809-0a0b0c0d2b10"
CHAR_SEND: Final = "00010203-0405-0607-0809-0a0b0c0d2b11"

# "Service changed" characteristic for GATT structure changes
SERVICE_CHANGED: Final = "00001801-0000-1000-8000-00805f9b34fb"
CHAR_CHANGED: Final = "00002a05-0000-1000-8000-00805f9b34fb"

# No idea what these are
UNK_SERVICE_23: Final = "02f00000-0000-0000-0000-00000000fe00"
UNK_CHAR_23_30: Final = "02f00000-0000-0000-0000-00000000ff00"  # ""
# bleak: "operation is not supported" when trying to read or subscribe to this.
UNK_CHAR_23_32: Final = "02f00000-0000-0000-0000-00000000ff01"
UNK_CHAR_23_26: Final = "02f00000-0000-0000-0000-00000000ff02"  # "ntf_enable"
UNK_CHAR_23_24: Final = "02f00000-0000-0000-0000-00000000ff03"  # ""

CMD_READ: Final = 0xAA
"""Read from a register."""
CMD_WRITE: Final = 0x33
"""Write to a register."""
CMD_MULTI: Final = 0xA3
"""Multi-part write."""

REG_POWER: Final = 0x01
"""Power register. 0x01 is on, otherwise off."""
REG_MULTI_ACK: Final = 0x02
"""Pseudo-register that multi-part write commands ACK from."""
REG_DIMMER: Final = 0x04
"""Dimmer register. Govee seems to use percent values."""
REG_MODE: Final = 0x05
"""Mode register. Requires special handling."""
REG_VERSION: Final = 0x06
"""Hardware version."""
REG_INFO: Final = 0x07
"""Infomation multi-register."""
REG_INFO_MAC_UNK: Final = REG_INFO << 8 | 0x02
"""MAC address in little-endian + 2 unknown bytes."""
REG_INFO_HWVER: Final = REG_INFO << 8 | 0x03
"""Hardware version info subregister."""
REG_INFO_FWVER: Final = REG_INFO << 8 | 0x04
"""Firmware version info subregister."""
REG_INFO_MAC: Final = REG_INFO << 8 | 0x06
"""MAC address in little-endian."""
REG_RESTART: Final = 0x0E
"""Restart register. Writing a reason will restart the light."""
REG_BUFFER: Final = 0xA5  # Multi-register color buffer
"""
Color buffer multi-register. Each sub-register is 3 4-byte colors,
brightness + rgb. xx010101 is used as the undefined color. They retain
garbage from previous commands if not overwritten.
"""

# Unknown registers which notify when read
UNK_REG_0f: Final = 0x0F
UNK_REG_11: Final = 0x11
UNK_REG_12: Final = 0x12
UNK_REG_23: Final = 0x23
UNK_REG_40: Final = 0x40
UNK_REG_41: Final = 0x41
UNK_REG_a3: Final = 0xA3
UNK_REG_ee: Final = 0xEE
UNK_REG_ef: Final = 0xEF
UNK_REG_ff: Final = 0xFF

MULTI_REG: Final = {REG_INFO, REG_BUFFER}
"""Registers with multiple sub-registers."""

MODE_SCENE: Final = 0x04
"""Mode register scene value."""

# Segment commands
MODE_SEGMENT: Final = 0x15
"""Mode register segment value."""
MODE_SEGMENT_COLOR: Final = 0x01
"""Mode register segment command for setting color."""
MODE_SEGMENT_BRIGHT: Final = 0x02
"""Mode register segment command for setting brightness."""

SEGMENT_COUNT: Final = 15
"""Number of discrete segments in the light."""
SEGMENT_OFFSET: Final = 3
"""Segment offset in the color buffer."""

SUBREGISTER_COLORS: Final = 3
"""Number of colors in a color buffer sub-register."""

MIN_BRIGHT: Final = 0
"""Minimum brightness value."""
MAX_DIM: Final = 100
"""Maximum brightness value."""
MAX_MESSAGE: Final = 20
"""Largest size of a message (including checksum)."""

SCENE_ID: Final = {
    # From reverse engineering repo
    "sunrise": 0,
    "sunset": 1,
    # Unused: 2, 3
    "movie": 4,
    "dating": 5,
    # Unused: 6
    "romantic": 7,
    # "twinkle": 8,
    # "candlelight": 9,
    # "breathe": 10,
    # Unused: 11-13
    # "snowflake": 15,
    # "energetic": 16,
    # Unused: 17-19
    # "crossing": 20,
    # "rainbow": 21,
    # From my own reverse engineering
    "illumination": 0x3F,
    "cheerful": 0x40,
}
SCENE_NAME: Final = {v: k for k, v in SCENE_ID.items()}

# All of these from the other repo
MUSIC_ID: Final = {"energetic": 0, "spectrum": 1, "rolling": 2, "rhythm": 3}

STYLE_ID: Final = {"fade": 0, "jumping": 1, "flicker": 2, "marquee": 3, "music": 4}

MODE_ID: Final = {
    "whole": 0,
    "subsection": 1,
    "circulation": 2,
    "straight": 3,
    "gathered": 4,
    "dispersive": 5,
    "spectrum": 6,
    "rolling": 7,
    "rhythm": 8,
}


class ARGB(NamedTuple):
    brightness: int = 0
    r: int = 0
    g: int = 0
    b: int = 0


type RGB = tuple[int, int, int]
type BytesLike = None | bool | int | Iterable[int]

COLOR_UNDEFINED: Final[RGB] = (1, 1, 1)


@dataclass
class ColorMode:
    """Technically just segment mode with all colors the same."""

    color: ARGB


@dataclass
class SegmentMode:
    segments: list[ARGB]


@dataclass
class SceneMode:
    code: int
    name: str


type Mode = ColorMode | SegmentMode | SceneMode


def assert_range(v, name, min, max):
    if v < min or v > max:
        raise ValueError(f"{name} must be {min}-{max}")


def assert_rgb(rgb: RGB):
    assert_range(rgb[0], "Red", 0, 255)
    assert_range(rgb[1], "Green", 0, 255)
    assert_range(rgb[2], "Blue", 0, 255)


def conv_byte(*bs: BytesLike) -> Generator[int, None, None]:
    for b in bs:
        match b:
            case int() | bool():
                yield int(b)
            case None:
                pass
            case _ if isinstance(b, Iterable):
                yield from b

            case _:
                raise TypeError(f"Invalid type for byte conversion: {type(b)}")


def print_conv(*bs: BytesLike):
    conv = bytes(conv_byte(*bs))
    print(bytes([*conv, *[0] * (MAX_MESSAGE - 1 - len(conv)), checksum(conv)]).hex())


def register(reg: int):
    if reg > 0xFF:
        yield reg >> 8
    yield reg & 0xFF


def checksum(data: bytes | bytearray):
    cs = 0
    for b in data:
        cs ^= b
    return cs


def batch_bytes(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield data[i : i + size]


@dataclass
class EffectRuleSchema:
    hardVersion: str
    softVersion: str
    wifiSoftVersion: str


@dataclass
class SpecialSchema:
    code: int
    param: bytes
    speed: list[dict]


@dataclass
class EffectSchema:
    code: int
    param: bytes
    diyCode: int
    diyParam: bytes
    rules: list[EffectRuleSchema]
    special: list[SpecialSchema]

    def summary(self):
        if self.diyCode:
            return {"code": self.code, "diy": self.diyCode}
        else:
            return self.code


@dataclass
class SceneSchema:
    name: str
    category: str
    effects: list[EffectSchema]
    hint: str

    def summary(self, name: bool = False):
        summary = [effect.summary() for effect in self.effects]
        if len(summary) == 1:
            summary = summary[0]
        return {self.name: summary} if name else summary


@dataclass
class CategorySchema:
    name: str
    scenes: dict[str, SceneSchema]

    def summary(self, name: bool = False):
        summary = {scene.name: scene.summary(name) for scene in self.scenes.values()}
        return {self.name: summary} if name else summary


def detitle(title: str):
    return re.sub(r"\W", "", title.lower())


class SceneInfo:
    by_id: dict[int, SceneSchema]
    scenes: dict[str, SceneSchema]
    categories: dict[str, CategorySchema]

    def __init__(self, fname: str):
        try:
            with open(fname) as f:
                raw: ConsolidateCategories = json.load(f)
        except FileNotFoundError:
            _LOGGER.warning("Scene info not found: %s", fname)
            raw = {}
        except json.JSONDecodeError:
            _LOGGER.warning("Scene info not valid JSON: %s", fname)
            raw = {}

        self.by_id = {}
        self.scenes = {}
        self.categories = {}
        for cat_title, scenes in raw.items():
            cat_name = detitle(cat_title)
            cat_scenes = {}
            self.categories[cat_name] = CategorySchema(cat_title, cat_scenes)
            for scene_title, scene in scenes.items():
                scene_name = detitle(scene_title)
                scene_effects: list[EffectSchema] = []
                new = SceneSchema(
                    scene_title, cat_title, scene_effects, scene.get("hint", "")
                )
                self.scenes[scene_name] = new
                cat_scenes[scene_name] = new

                for effect in scene["effects"]:
                    if code := effect.get("code"):
                        self.by_id[code] = new

                    scene_effects.append(
                        EffectSchema(
                            code,
                            binascii.a2b_base64(effect["param"]),
                            effect.get("diyCode", 0),
                            binascii.a2b_base64(effect.get("diyParam", "")),
                            [
                                EffectRuleSchema(
                                    rule.get("hardVersion", ""),
                                    rule.get("softVersion", ""),
                                    rule.get("wifiSoftVersion", ""),
                                )
                                for rule in effect.get("rules", [])
                            ],
                            [
                                SpecialSchema(
                                    special["code"],
                                    binascii.a2b_base64(special["param"]),
                                    special.get("speed", []),
                                )
                                for special in effect.get("special", [])
                            ],
                        )
                    )

    def get_scene(self, name: str | int) -> SceneSchema | None:
        """Get a scene by id, name, or category - name."""
        if isinstance(name, int):
            return self.by_id.get(name)

        cat, _, scene = detitle(name).partition("-")
        if scene == "":
            return self.scenes.get(cat)

        if cat := self.categories.get(cat):
            return cat.scenes.get(scene)

        return None

    def summary(self):
        return {cat.name: cat.summary() for cat in self.categories.values()}


def parse_packet(data: bytes):
    cmd = data[0]
    ro = 2 + (data[1] in MULTI_REG)
    return cmd, data[:ro], data[ro:]


class GoveeLight:
    tg: asyncio.TaskGroup
    """Task group for managing futures."""
    client: BleakClient
    """Bluetooth client."""
    futures: defaultdict[bytes, list[asyncio.Future[bytes]]]
    """Map of commands to their listeners."""
    subscribed: defaultdict[str, list[Callable]]
    """Map of event subscriptions."""
    state: dict[int, bytes]
    """Current state of the light."""

    def __init__(self, name: str, address: str):
        m = GOVEE_RE.match(name)
        if m is None:
            raise ValueError(f"Invalid Govee name: {name}")

        self.name = m[1]
        self.tg = asyncio.TaskGroup()
        self.client = BleakClient(address)
        self.futures = defaultdict(list)
        self.subscribed = defaultdict(list)
        self.state = {}

    async def __aenter__(self):
        await self.tg.__aenter__()
        await self.client.connect()
        _LOGGER.info("Connected to Govee BLE device: %s", self.client.address)
        await self.client.start_notify(CHAR_RECV, self._on_notify)
        return self

    async def __aexit__(self, *exc):
        await self.tg.__aexit__(*exc)
        await self.client.disconnect()

    async def _on_notify(self, sender, ba: bytearray):
        if len(ba) < 3:
            return _LOGGER.error("Invalid data: %s", ba.hex())

        if checksum(ba) != 0:
            self.emit("error", "checksum", bytes(ba))
            return _LOGGER.error("Checksum error: %s", ba.hex())

        data = bytes(ba[:-1].rstrip(b"\0")) or b"\0"
        cmd = data[0]

        if cmd not in {CMD_READ, CMD_WRITE, CMD_MULTI}:
            return _LOGGER.error("Unexpected data: %s", data.hex())

        cmd, key, val = parse_packet(data)
        if cmd == CMD_READ:
            self.state[int.from_bytes(key[1:])] = bytes(val)
            if key[1] != REG_POWER:
                _LOGGER.debug("Notify (%s): %s", key.hex(), val.hex())
        elif cmd == CMD_MULTI:
            _LOGGER.debug("Notify (%s): %s", key.hex(), val.hex())
        # Note: CMD_WRITE has ACK but no value

        self.emit("recv", cmd, int.from_bytes(key[1:]), val)

        if fs := self.futures.get(key):
            fs.pop(0).set_result(val)

    @overload
    def on(
        self, event: Literal["recv"], callback: Callable[[int, int, bytes], Awaitable]
    ) -> None: ...
    @overload
    def on(
        self, event: Literal["send"], callback: Callable[[int, int, bytes], Awaitable]
    ) -> None: ...
    @overload
    def on(
        self, event: Literal["error"], callback: Callable[[str, bytes], Awaitable]
    ) -> None: ...
    def on(self, event: str, callback: Callable) -> None:
        self.subscribed[event].append(callback)

    @overload
    def emit(
        self, event: Literal["send"], cmd: int, reg: int, data: bytes, /
    ) -> None: ...
    @overload
    def emit(
        self, event: Literal["recv"], cmd: int, reg: int, data: bytes, /
    ) -> None: ...
    @overload
    def emit(self, event: Literal["error"], reason: str, data: bytes, /) -> None: ...
    def emit(self, event: Literal["send", "recv", "error"], *args):
        for callback in self.subscribed[event]:
            self.tg.create_task(callback(*args))

    async def send_raw(self, data: bytes):
        """Raw send with zero padding and checksum."""
        if len(data) >= MAX_MESSAGE:
            raise ValueError("Command too long")
        k, v, d = parse_packet(data)
        self.emit("send", k, int.from_bytes(v[1:]), d)
        data += bytes([*[0] * (19 - len(data)), checksum(data)])
        await self.client.write_gatt_char(CHAR_SEND, data, response=True)

    async def send_data(self, *parts: BytesLike):
        """Send data with zero padding and checksum."""
        await self.send_raw(bytes(conv_byte(*parts)))

    async def send_read(self, reg: int):
        """Raw read without awaiting response."""
        await self.send_data(CMD_READ, *register(reg))

    async def send_write(self, reg: int, *parts: BytesLike):
        """Raw write without awaiting response."""
        await self.send_data(CMD_WRITE, *register(reg), *parts)

    async def heartbeat(self):
        """
        The app reads the power every 2 seconds which acts as a heartbeat.
        This also allows us to check if the power button has been pressed.
        """
        while True:
            await asyncio.sleep(2)
            try:
                # Don't need to heartbeat if we're waiting for a response
                if not any(self.futures.values()):
                    await self.read(REG_POWER)
            except TimeoutError:
                _LOGGER.warning("Heartbeat timeout")

    def keepalive(self):
        """Register the heartbeat task."""
        return self.tg.create_task(self.heartbeat())

    async def send_multi(self, data: bytes):
        PAYLOAD = MAX_MESSAGE - 1 - 1 - 1  # command, packet, checksum
        INIT = PAYLOAD - 1 - 1 - 1  # 01, count, 02
        chunks = (len(data) - INIT + (PAYLOAD - 1)) // PAYLOAD

        await self.send_data(CMD_MULTI, 0, 1, chunks + 1, 2, data[:INIT])

        # All others: a3 i ...data checksum
        for i, chunk in enumerate(batch_bytes(data[INIT:], PAYLOAD), 1):
            if i == chunks:
                i = 0xFF  # Last packet has index 0xff
            await self.send_data(CMD_MULTI, i, chunk)

    async def _ack(self, cmd: int, reg: int, send: Coroutine) -> bytes:
        """Read with response or write with ACK."""
        target = bytes([cmd, *register(reg)])
        future = asyncio.Future()
        self.futures[target].append(future)
        await send
        async with asyncio.timeout(5):
            return await future

    async def read(self, reg: int) -> bytes:
        """Read from a register."""
        return await self._ack(CMD_READ, reg, self.send_read(reg))

    async def write(self, reg: int, *parts: BytesLike):
        """Write to a register."""
        # Invalidate the cache
        self.state.pop(reg, None)
        await self._ack(CMD_WRITE, reg, self.send_write(reg, *parts))

    async def multi(self, data: bytes):
        """Write to a multi-register."""
        await self._ack(CMD_MULTI, REG_MULTI_ACK, self.send_multi(data))

    async def cache_read(self, reg: int):
        if reg in self.state:
            return self.state[reg]
        try:
            return await self.read(reg)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout reading %s", reg)
            if reg in self.state:
                return self.state[reg]
            raise

    async def get_buffer(self, index: int):
        r, c = divmod(index, 3)
        buf = await self.cache_read(REG_BUFFER << 8 | r)
        c *= 4
        return buf[c : c + 4]

    async def get_power(self) -> bool:
        return bool((await self.cache_read(REG_POWER) or b"\0")[0])

    async def set_power(self, value: bool):
        await self.write(REG_POWER, bytes([value]))

    async def get_dimmer(self) -> float:
        return (await self.cache_read(REG_DIMMER) or b"\0")[0] / MAX_DIM

    async def set_dimmer(self, value: float):
        assert_range(value, "Dimmer", 0, 1)
        await self.write(REG_DIMMER, int(value * MAX_DIM))

    async def get_mode(self) -> Mode:
        mode = await self.cache_read(REG_MODE)
        m = mode[0] if mode else MODE_SCENE
        if m == MODE_SCENE:
            index = int.from_bytes(mode[1:], "little")
            name = SCENE_NAME.get(index, "Unknown")
            return SceneMode(index, name)

        if m == MODE_SEGMENT:
            segments = await self.get_segments()
            s0 = segments[0]
            if all(s == s0 for s in segments):
                return ColorMode(s0)
            else:
                return SegmentMode(segments)

        raise ValueError(f"Unknown mode: {m}")

    async def get_reason(self) -> int:
        return (await self.cache_read(REG_POWER) or b"\0")[0]

    async def get_version(self) -> str:
        return (await self.cache_read(REG_VERSION)).decode()

    async def get_mac(self) -> str:
        return (await self.cache_read(REG_INFO_MAC)).hex(":")

    async def get_hwver(self) -> str:
        return (await self.cache_read(REG_INFO_HWVER)).decode()

    async def get_fwver(self) -> str:
        return (await self.cache_read(REG_INFO_FWVER)).decode()

    @cached_property
    def scene_info(self) -> SceneInfo:
        return SceneInfo(f"{self.name}.json")

    def expand_segments(self, segments: int):
        return (segments & 0x7FFF).to_bytes(2, "little")

    async def restart(self, reason: int = 1):
        """Restart the light."""
        await self.send_write(REG_RESTART, reason)

    async def get_scene(self):
        """Get the current scene of the light."""
        mode = await self.get_mode()
        if isinstance(mode, SceneMode):
            return self.scene_info.get_scene(mode.code)
        return None

    async def set_scene(self, scene: str | int):
        """Set the scene of the light."""

        # Done by the app, probably unnecessary
        _ = await self.read(REG_POWER)

        if si := self.scene_info.get_scene(scene):
            code = si.effects[0].code
            param = si.effects[0].param
        else:
            raise ValueError(f"Unknown scene: {scene}")

        if param:
            await self.multi(param)
        await self.write(REG_MODE, MODE_SCENE, code.to_bytes(2, "little"))

    async def get_segments(self, segments: int = -1):
        """Get the colors of all segments."""
        segments &= 0x7FFF
        return [
            ARGB(*await self.get_buffer(i + SEGMENT_OFFSET))
            for i in range(SEGMENT_COUNT)
            if segments & (1 << i)
        ]

    async def set_color(self, rgb: RGB, segments: int = -1):
        """Set the color of the light."""
        assert_rgb(rgb)
        await self.write(
            REG_MODE,
            MODE_SEGMENT,
            MODE_SEGMENT_COLOR,
            *rgb,
            b"\0" * 5,
            self.expand_segments(segments),
        )

    async def set_brightness(self, value: float, segments: int = -1):
        """Set the brightness of the light."""
        assert_range(value, "Brightness", 0, 1)
        await self.write(
            REG_MODE,
            MODE_SEGMENT,
            MODE_SEGMENT_BRIGHT,
            round(value * (MAX_DIM - MIN_BRIGHT)) + MIN_BRIGHT,
            self.expand_segments(segments),
        )


@asynccontextmanager
async def scan():
    print("Scanning...")
    for dev in await BleakScanner.discover():
        if dev.name is None:
            continue
        if GOVEE_RE.match(dev.name):
            async with GoveeLight(dev.name, dev.address) as light:
                yield light
    raise RuntimeError("No Govee light found")


def asm_cmd(cmd: Optional[str | bytes]):
    if cmd is None:
        return b""

    if isinstance(cmd, (bytes, bytearray)):
        return bytes(cmd)

    cmd = re.sub(
        r"^(r(?:ead)?|w(?:rite)?)", lambda m: {"r": "aa", "w": "33"}[m[1]], cmd
    )
    cmd = re.sub(r"\bscene\b", "0504", cmd)
    cmd = re.sub(r"\bparam\b", "04", cmd)
    print("Asm:", cmd)
    return bytes.fromhex(cmd.replace(" ", ""))


def parse_cmd(cmd: str):
    cmd = cmd.lower()
    if cmd.startswith("restart"):
        yield asm_cmd("0e" + cmd[7:])
        return

    parts = cmd.split("/")
    if len(parts) == 1:
        change = parts[0]
        prefix = b""
        suffix = b""
    elif len(parts) == 2:
        prefix = asm_cmd(parts[0])
        change = parts[1]
        suffix = b""
    elif len(parts) == 3:
        prefix = asm_cmd(parts[0])
        change = parts[1]
        suffix = asm_cmd(parts[2])
    else:
        raise ValueError("Too many assembly parts")

    for part in change.split(","):
        if m := RANGE_RE.match(part):
            start, end = m.groups()
            if end is None:
                yield asm_cmd(prefix + asm_cmd(start))
            else:
                for i in range(int(start, 16), int(end, 16) + 1):
                    yield prefix + bytes([i]) + suffix
        else:
            yield prefix + asm_cmd(part) + suffix


async def main():
    last = 0
    async with scan() as light:
        _ = light.keepalive()

        while True:
            try:
                cmd = await ainput("Command: ")
                cmd = cmd.strip()
                if not cmd:
                    continue

                if cmd == "state":
                    print(light.state)
                    continue

                for c in parse_cmd(cmd):
                    await light.send_raw(c)
            except (KeyboardInterrupt, EOFError):
                now = time.time()
                if now - last < 5:
                    break
            except Exception:
                now = time.time()
                if now - last < 5:
                    raise

                tb.print_exc()
                last = now


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
