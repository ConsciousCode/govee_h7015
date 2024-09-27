import json
import re
from typing import cast
import asyncio
import traceback as tb

import aiomqtt

from govee import RGB, ColorMode, GoveeLight, SceneMode, SegmentMode, parse_cmd, scan

TASMOTA_COLORS = [
    tuple(bytes.fromhex(color)) for color in [
        'FF0000', '00FF00', '0000FF', 'FFA500',
        '90EE90', 'ADD8E6', 'FFBF00', '00FFFF',
        '800080', 'FFFF00', 'FFC0CB', 'FFFFFF'
    ]
]
TASMOTA_NAMES = [
    'red', 'green', 'blue', 'orange',
    'lightgreen', 'lightblue', 'amber', 'cyan',
    'purple', 'yellow', 'pink', 'white'
]

HEX_RE = re.compile(r'0x([\da-f]+)')
CMD_RE = re.compile(r'([a-z]+)(\d+)?')
COLOR_RE = re.compile(r'''
    \#?([0-9a-f]{3}) # Hexadecimal
   |\#?([0-9a-f]{6}) # Hexadecimal
   |(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3}) # Decimal
   |(\d{1,3})        # Indexed
   |([\w\s]+)        # Named
''', re.X)

def str_color(r: int, g: int, b: int):
    return f"#{r:02X}{g:02X}{b:02X}"

def fuzzy_int(value: str) -> int:
    if m := HEX_RE.match(value):
        return int(m[1], 16)
    else:
        return int(value)

async def handle_command(dev: GoveeLight, cmd: str, data: str):
    m = CMD_RE.match(cmd)
    if m is None:
        return {"ERROR": f"Invalid command: {cmd}"}
    
    try:
        match m[1]:
            case "power":
                match data.strip().lower():
                    case "toggle":
                        await dev.set_power(not await dev.get_power())
                    case "0"|"off"|"false":
                        await dev.set_power(False)
                    case "1"|"on"|"true":
                        await dev.set_power(True)
                    case "": pass
                    
                    case _:
                        return {"ERROR": f"Invalid power value: {data}"}
                
                return {"Power": await dev.get_power()}
            
            case "dimmer":
                if data := data.strip():
                    try:
                        dimmer = float(data)
                    except ValueError:
                        return {"ERROR": f"Invalid dimmer value: {data}"}
                    
                    if dimmer > 1:
                        dimmer = dimmer / 100
                    await dev.set_dimmer(dimmer)
                
                return {"Dimmer": await dev.get_dimmer()}
            
            case "mode":
                match await dev.get_mode():
                    case ColorMode(color):
                        mode = {
                            "mode": "color",
                            "brightness": color.brightness,
                            "color": str_color(color.r, color.g, color.b)
                        }
                    
                    case SegmentMode(segments):
                        mode = {
                            "mode": "segment",
                            "segments": [
                                {
                                    "color": str_color(r, g, b),
                                    "brightness": brightness
                                } for brightness, r, g, b in segments
                            ]
                        }
                    
                    case SceneMode(code, name):
                        mode = {
                            "mode": "scene",
                            "code": code,
                            "name": name
                        }
                    
                    case m:
                        raise NotImplementedError(f"Unknown mode: {m}")
                
                return {"Mode": mode}
            
            case "version":
                match m[2]:
                    case '1': version = await dev.get_version()
                    case '2': version = await dev.get_hwver()
                    case '3': version = await dev.get_fwver()
                    case _:
                        version = (
                            await dev.get_version(),
                            await dev.get_hwver(),
                            await dev.get_fwver()
                        )
                
                return {"Version": version}
            
            case "mac":
                return {"MAC": await dev.get_mac()}
            
            case "restart":
                if data := data.strip():
                    try:
                        reason = fuzzy_int(data)
                    except ValueError:
                        return {"ERROR": "Invalid reason"}
                    await dev.restart(reason)
                
                return {"Restart": await dev.get_reason()}
            
            case "status":
                if m[3] is None or m[3] == "0":
                    return {
                        "Power": await dev.get_power(),
                        "Dimmer": await dev.get_dimmer(),
                        "Mode": await dev.get_mode(),
                        "Version": (
                            await dev.get_version(),
                            await dev.get_hwver(),
                            await dev.get_fwver()
                        ),
                        "MAC": await dev.get_mac(),
                        "Restart": await dev.get_reason()
                    }
            
            case "scene":
                data = data.strip().lower()
                try:
                    scene = fuzzy_int(data)
                except ValueError:
                    scene = data
                
                try:
                    await dev.set_scene(scene)
                except ValueError:
                    return {"ERROR": f"Invalid scene: {data}"}
                
                if scene := await dev.get_scene():
                    return {"Scene": scene.summary(name=True)}
                else:
                    return {"Scene": None}
            
            case "scenes":
                return {"Scenes": dev.scene_info.summary()}
            
            case "brightness":
                if data := data.strip().lower():
                    try:
                        brightness = float(data)
                        if brightness > 1:
                            brightness = brightness / 100
                    except ValueError:
                        return {"ERROR": f"Invalid brightness value: {data}"}
                    
                    await dev.set_brightness(brightness, 1<<int(m[2] or 0))
                    return {"Brightness": brightness}
                else:
                    if segment := int(m[2] or 0):
                        color = await dev.get_segments(1<<segment)
                        return {"Brightness": color[0].brightness}
                    else:
                        mode = await dev.get_mode()
                        if isinstance(mode, ColorMode):
                            color = mode.color
                            return {"Brightness": color.brightness}
                        else:
                            return {"Brightness": None}
            
            case "color":
                if data := data.strip().lower():
                    if c := COLOR_RE.match(data):
                        if cc := c[1]:
                            color = tuple(0x11*b for b in bytes.fromhex(cc))
                        elif cc := c[2]:
                            color = tuple(bytes.fromhex(cc))
                        elif cc := c[6]:
                            color = TASMOTA_COLORS[int(cc)]
                        elif cc := c[7]:
                            cc = cc.replace(' ', '')
                            color = TASMOTA_COLORS[TASMOTA_NAMES.index(cc)]
                        else:
                            r, g, b = c.groups()[3:6]
                            color = (int(r), int(g), int(b))
                        
                        await dev.set_color(cast(RGB, color), 1<<int(m[2] or 0))
                        return {"Color": str_color(*color)}
                    else:
                        return {"ERROR": f"Invalid color: {data!r}"}
                else:
                    mode = await dev.get_mode()
                    if isinstance(mode, ColorMode):
                        color = mode.color
                        return {"Color": str_color(color.r, color.g, color.b)}
                    else:
                        return {"Color": None}
            
            case "peek":
                start, _, end = data.partition(':')
                if end:
                    try:
                        start = int(start, 16)
                        end = int(end, 16)
                    except ValueError:
                        return {"ERROR": "Invalid range"}
                    
                    peeks = []
                    try:
                        for reg in range(start, end+1):
                            peeks.append((await dev.read(reg)).hex())
                        return {"Peek": peeks}
                    except TimeoutError:
                        return {"ERROR": "Timeout", "Peek": peeks}
                else:
                    try:
                        reg = int(data, 16)
                    except ValueError:
                        return {"ERROR": "Invalid register"}
                    
                    return {"Peek": (await dev.read(reg)).hex()}
            
            case "poke":
                try:
                    rs, ds = data.split(' ', 1)
                except ValueError:
                    return {"ERROR": "Missing data"}
                
                try:
                    reg = int(rs, 16)
                except ValueError:
                    return {"ERROR": "Invalid register"}
                
                try:
                    bd = bytes.fromhex(ds)
                except ValueError:
                    return {"ERROR": "Invalid data"}
                
                await dev.write(reg, bd)
                return {"Poke": None}
            
            case "multi":
                try:
                    bd = bytes.fromhex(data.replace(' ', ''))
                except ValueError:
                    return {"ERROR": "Invalid data"}
                
                await dev.multi(bd)
                return {"Multi": None}
            
            case "raw":
                try:
                    await dev.send_raw(bytes.fromhex(data))
                    return {"Raw": None}
                except ValueError:
                    return {"ERROR": "Invalid data"}
            
            case "asm":
                for asm in parse_cmd(data):
                    await dev.send_raw(asm)
                return {"ASM": None}
            
            case "buffer":
                return {"Buffer": dev.color_buffer}
        
        return {"ERROR": f"Unknown command: {cmd}"}
    except TimeoutError:
        return {"ERROR": "Timeout"}
    except Exception:
        return {"ERROR": "Uncaught", "Traceback": tb.format_exc()}

async def govee_mqtt(
        dev: GoveeLight,
        broker: str = "theseus.home.arpa",
        topic: str = "govee",
        command: str = "cmnd",
        stat: str = "stat",
        result: str = "RESULT"
    ):
    prefix = f"{command}/{topic}/"
    command_topic = f"{prefix}+"
    result_topic = f"{stat}/{topic}/{result}"
    async with aiomqtt.Client(broker) as client:
        await client.subscribe(command_topic)
        
        async for message in client.messages:
            match message.payload:
                case bytes(bp): payload = bp.decode()
                case str(payload): pass
                case up:
                    raise ValueError(f"What is {up!r}?")
            
            await client.publish(result_topic, json.dumps(
                await handle_command(
                    dev,
                    message.topic.value.removeprefix(prefix),
                    payload
                )
            ))

MAC = 'D3:39:32:35:1A:88'

async def main():
    async with scan(MAC) as light:
        light.keepalive()
        await govee_mqtt(light)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass