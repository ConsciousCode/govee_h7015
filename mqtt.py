import json
import re
from typing import cast
import asyncio
import traceback as tb

import aiomqtt

import govee

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

class GoveeMQTT:
    def __init__(self, dev: govee.GoveeLight, broker: str, topic: str="govee", command: str="cmnd", stat: str="stat", result: str="RESULT", notify: str="NOTIFY", ack: str="ACK", error: str="ERROR", send: str="SEND"):
        self.prefix = f"{command}/{topic}/"
        self.client = aiomqtt.Client(broker)
        self.dev = dev
        self.broker = broker
        self.topic = topic
        self.command = f"{self.prefix}+"
        stat = f"{stat}/{topic}/"
        self.send = stat + send
        self.notify = stat + notify
        self.ack = stat + ack
        self.result = stat + result
        self.error = stat + error
    
    async def __aenter__(self):
        await self.client.__aenter__()
        await self.client.subscribe(self.command)
        self.dev.on("recv", self.on_recv)
        self.dev.on("send", self.on_send)
        self.dev.on("checksum", self.on_error) # type: ignore
        self.dev.on("timeout", self.on_error) # type: ignore
        self.dev.on("unexpected", self.on_error) # type: ignore
        return self
    
    async def __aexit__(self, *exc):
        return await self.client.__aexit__(*exc)
    
    async def serve(self):
        async for message in self.client.messages:
            match message.payload:
                case bytes(bp): payload = bp.decode()
                case str(payload): pass
                case up: raise ValueError(f"What is {up!r}?")
            
            await self.client.publish(self.result, json.dumps(
                await self.handle_command(
                    message.topic.value.removeprefix(self.prefix),
                    payload
                )
            ))
    
    async def on_recv(self, cmd: int, key: int, data: bytes):
        match cmd:
            case govee.CMD_READ:
                await self.client.publish(self.notify, json.dumps({
                    "register": key,
                    "data": data.hex()
                }))
            
            case govee.CMD_WRITE|govee.CMD_MULTI:
                await self.client.publish(self.ack, json.dumps({
                    "data": bytes([key, *data]).hex()
                }))
            
            case _:
                await self.client.publish(self.error, json.dumps({
                    "message": "Unknown message from device.",
                    "data": bytes([cmd, key, *data]).hex()
                }))
    
    async def on_send(self, cmd: int, key: int, data: bytes):
        await self.client.publish(self.send, json.dumps({
            "cmd": cmd,
            "register": key,
            "data": data.hex()
        }))
    
    async def on_error(self, *args):
        await self.client.publish(self.error, json.dumps({
            "data": args
        }))
    
    async def handle_command(self, cmd: str, data: str):
        m = CMD_RE.match(cmd)
        if m is None:
            return {"ERROR": f"Invalid command: {cmd}"}
        
        try:
            match m[1]:
                case "power":
                    match data.strip().lower():
                        case "toggle":
                            await self.dev.set_power(not await self.dev.get_power())
                        case "0"|"off"|"false":
                            await self.dev.set_power(False)
                        case "1"|"on"|"true":
                            await self.dev.set_power(True)
                        case "": pass
                        
                        case _:
                            return {"ERROR": f"Invalid power value: {data}"}
                    
                    return {"Power": await self.dev.get_power()}
                
                case "dimmer":
                    if data := data.strip():
                        try:
                            dimmer = float(data)
                        except ValueError:
                            return {"ERROR": f"Invalid dimmer value: {data}"}
                        
                        if dimmer > 1:
                            dimmer = dimmer / 100
                        await self.dev.set_dimmer(dimmer)
                    
                    return {"Dimmer": await self.dev.get_dimmer()}
                
                case "mode":
                    match await self.dev.get_mode():
                        case govee.ColorMode(color):
                            mode = {
                                "mode": "color",
                                "brightness": color.brightness,
                                "color": str_color(color.r, color.g, color.b)
                            }
                        
                        case govee.SegmentMode(segments):
                            mode = {
                                "mode": "segment",
                                "segments": [
                                    {
                                        "color": str_color(r, g, b),
                                        "brightness": brightness
                                    } for brightness, r, g, b in segments
                                ]
                            }
                        
                        case govee.SceneMode(code, name):
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
                        case '1': version = await self.dev.get_version()
                        case '2': version = await self.dev.get_hwver()
                        case '3': version = await self.dev.get_fwver()
                        case _:
                            version = (
                                await self.dev.get_version(),
                                await self.dev.get_hwver(),
                                await self.dev.get_fwver()
                            )
                    
                    return {"Version": version}
                
                case "mac":
                    return {"MAC": await self.dev.get_mac()}
                
                case "restart":
                    if data := data.strip():
                        try:
                            reason = fuzzy_int(data)
                        except ValueError:
                            return {"ERROR": "Invalid reason"}
                        await self.dev.restart(reason)
                    
                    return {"Restart": await self.dev.get_reason()}
                
                case "status":
                    if m[3] is None or m[3] == "0":
                        return {
                            "Power": await self.dev.get_power(),
                            "Dimmer": await self.dev.get_dimmer(),
                            "Mode": await self.dev.get_mode(),
                            "Version": (
                                await self.dev.get_version(),
                                await self.dev.get_hwver(),
                                await self.dev.get_fwver()
                            ),
                            "MAC": await self.dev.get_mac(),
                            "Restart": await self.dev.get_reason()
                        }
                
                case "scene":
                    data = data.strip().lower()
                    try:
                        scene = fuzzy_int(data)
                    except ValueError:
                        scene = data
                    
                    try:
                        await self.dev.set_scene(scene)
                    except ValueError:
                        return {"ERROR": f"Invalid scene: {data}"}
                    
                    if scene := await self.dev.get_scene():
                        return {"Scene": scene.summary(name=True)}
                    else:
                        return {"Scene": None}
                
                case "scenes":
                    return {"Scenes": self.dev.scene_info.summary()}
                
                case "brightness":
                    if data := data.strip().lower():
                        try:
                            brightness = float(data)
                            if brightness > 1:
                                brightness = brightness / 100
                        except ValueError:
                            return {"ERROR": f"Invalid brightness value: {data}"}
                        
                        await self.dev.set_brightness(brightness, 1<<int(m[2] or 0))
                        return {"Brightness": brightness}
                    else:
                        if segment := int(m[2] or 0):
                            color = await self.dev.get_segments(1<<segment)
                            return {"Brightness": color[0].brightness}
                        else:
                            mode = await self.dev.get_mode()
                            if isinstance(mode, govee.ColorMode):
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
                            
                            await self.dev.set_color(cast(govee.RGB, color), 1<<int(m[2] or 0))
                            return {"Color": str_color(*color)}
                        else:
                            return {"ERROR": f"Invalid color: {data!r}"}
                    else:
                        mode = await self.dev.get_mode()
                        if isinstance(mode, govee.ColorMode):
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
                                peeks.append((await self.dev.read(reg)).hex())
                            return {"Peek": peeks}
                        except TimeoutError:
                            return {"ERROR": "Timeout", "Peek": peeks}
                    else:
                        try:
                            reg = int(data, 16)
                        except ValueError:
                            return {"ERROR": "Invalid register"}
                        
                        return {"Peek": (await self.dev.read(reg)).hex()}
                
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
                    
                    await self.dev.write(reg, bd)
                    return {"Poke": None}
                
                case "multi":
                    try:
                        bd = bytes.fromhex(data.replace(' ', ''))
                    except ValueError:
                        return {"ERROR": "Invalid data"}
                    
                    await self.dev.multi(bd)
                    return {"Multi": None}
                
                case "raw":
                    try:
                        await self.dev.send_raw(bytes.fromhex(data))
                        return {"Raw": None}
                    except ValueError:
                        return {"ERROR": "Invalid data"}
                
                case "asm":
                    for asm in govee.parse_cmd(data):
                        await self.dev.send_raw(asm)
                    return {"ASM": None}
            
            return {"ERROR": f"Unknown command: {cmd}"}
        except TimeoutError:
            return {"ERROR": "Timeout"}
        except Exception:
            return {"ERROR": "Uncaught", "Traceback": tb.format_exc()}

async def main():
    async with govee.scan() as light:
        light.keepalive()
        mqtt = GoveeMQTT(light,
            broker="theseus.home.arpa",
            topic="govee",
            command="cmnd",
            stat="stat",
            result="result"
        )
        async with mqtt:
            await mqtt.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass