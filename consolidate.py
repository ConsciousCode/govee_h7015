'''
Utility script for convering /jsons/* from [govee_ble_lights (Homeassistant)](https://github.com/Beshelmek/govee_ble_lights/) to a more straightforward format in /scenes/*.
'''
import os
import json
from typing import Literal, NotRequired, TypedDict

## Govee API dump types ##
# Note that the mispellings are from the API dump

class GoveeSceneRule(TypedDict):
    maxSoftVersion: Literal[""]
    minSoftVersion: Literal[""]
    maxHardVersion: Literal[""]
    minHardVersion: Literal[""]
    maxWifiSoftVersion: Literal[""]
    minWifiSoftVersion: Literal[""]
    maxWifiHardVersion: Literal[""]
    minWifiHardVersion: Literal[""]

class GoveeEffectRule(TypedDict):
    key: int
    hardVersion: str
    softVersion: str
    wifiSoftVersion: str

class GoveeSpeedInfo(TypedDict):
    config: Literal[""]
    speedIndex: Literal[0]
    supSpeed: Literal[False]

class GoveeSpecial(TypedDict):
    scenceParamId: int
    scenceParam: str
    cmdVersion: int
    supportSku: list[str]
    speedInfo: GoveeSpeedInfo

class GoveeEffect(TypedDict):
    scenceParamId: int
    scenceName: Literal["", "A", "B", "C", "D", "E", "F", "G", "H", "I"]
    scenceParam: str
    sceneCode: int
    specialEffect: list[GoveeSpecial]
    cmdVersion: int
    sceneType: int
    diyEffectCode: list # assert = []
    diyEffectStr: Literal[""]
    rules: list[GoveeEffectRule]
    speedInfo: GoveeSpeedInfo

class GoveeScene(TypedDict):
    sceneId: int
    iconUrls: list[str]
    sceneName: str
    analyticName: str
    sceneType: int
    sceneCode: int
    scenceCategoryId: int
    popUpPrompt: int
    scenesHint: str
    rule: GoveeSceneRule
    lightEffects: list[GoveeEffect]
    voiceUrl: str

class GoveeCategory(TypedDict):
    categoryId: int
    categoryName: str
    scenes: list[GoveeScene]

class GoveeData(TypedDict):
    categories: list[GoveeCategory]
    supportSpeed: Literal[0]

class GoveeRoot(TypedDict):
    message: Literal['success']
    status: Literal[200]
    data: GoveeData

## Consolidated types ##

class ConsolidateSpecial(TypedDict):
    code: int
    param: str
    speed: NotRequired[list[dict]]

class ConsolidateEffectRule(TypedDict):
    hardVersion: NotRequired[str]
    softVersion: NotRequired[str]
    wifiSoftVersion: NotRequired[str]

class ConsolidateEffect(TypedDict):
    code: int
    param: str
    diyCode: NotRequired[int]
    diyParam: NotRequired[str]
    rules: NotRequired[list[ConsolidateEffectRule]]
    special: NotRequired[list[ConsolidateSpecial]]

class ConsolidateScene(TypedDict):
    effects: list[ConsolidateEffect]
    hint: NotRequired[str]

type ConsolidateScenes = dict[str, ConsolidateScene]
type ConsolidateCategories = dict[str, ConsolidateScenes]

def assert_eq(a, b):
    if a == b:
        return
    raise AssertionError(f"{a!r} != {b!r}")

def assert_oneof(a, *b):
    if a in b:
        return
    raise AssertionError(f"{a!r} not in {b!r}")

class Consolidate:
    '''
    Read the homeassistant API dumps and consolidate them into more
    straightforward files. The asserts aren't authoritative, they're
    there to alert when an assumption about the API dump is incorrect
    and requires further investigation. eg a property is always a
    certain value and can be safely ignored, but if it isn't that value
    then it must have some unknown informational content.
    '''
    
    def special(self, special: GoveeSpecial):
        base: ConsolidateSpecial = {
            "code": special['scenceParamId'],
            "param": special['scenceParam']
        }
        
        if special['speedInfo']['supSpeed']:
            base['speed'] = json.loads(special['speedInfo']['config'])
        
        return base
    
    def effect_rules(self, rules: list[GoveeEffectRule]):
        for i, rule in enumerate(rules):
            assert_eq(rule['key'], i)
            base: ConsolidateEffectRule = {}
            for key in ('hardVersion', 'softVersion', 'wifiSoftVersion'):
                if rule[key]:
                    base[key] = rule[key]
            yield base
    
    def effect(self, effect: GoveeEffect):
        # No idea what this could be
        assert_oneof(effect['scenceName'], "", *"ABCDEFGHI")
        # ignoring:
        # - scenceParamId - Internal identifier?
        # - cmdVersion - Unknown significance
        # - sceneType - Unknown significance
        base: ConsolidateEffect = {
            "code": effect['sceneCode'],
            "param": effect['scenceParam']
        }
        if diyStr := effect['diyEffectStr']:
            base['diyParam'] = diyStr
        if diyCode := effect['diyEffectCode']:
            base['diyCode'] = diyCode[0]
        if rules := effect['rules']:
            base['rules'] = list(self.effect_rules(rules))
        if special := list(map(self.special, effect['specialEffect'])):
            base['special'] = special
        
        return base
    
    def scene(self, scene: GoveeScene):
        assert_eq(scene['sceneName'], scene['analyticName'])
        assert_eq(scene['rule'], {
            "maxSoftVersion": "",
            "minSoftVersion": "",
            "maxHardVersion": "",
            "minHardVersion": "",
            "maxWifiSoftVersion": "",
            "minWifiSoftVersion": "",
            "maxWifiHardVersion": "",
            "minWifiHardVersion": ""
        })
        assert_eq(scene['voiceUrl'], "")
        
        # ignoring:
        # - icons - List of URLs, kind of ugly, useless, and probably copyrighted
        # - sceneType - Unknown significance
        # - sceneCode - Internal identifier?
        # - scenceCategoryId - Internal category id
        # - popUpPrompt - Unknown significance, {0, 1, 2}?
        
        base: ConsolidateScene = {
            "effects": list(map(self.effect, scene['lightEffects']))
        }
        if hint := scene['scenesHint']:
            base['hint'] = hint
        
        return base
    
    def category(self, model: str, cat: GoveeCategory):
        base: ConsolidateScenes = {}
        for scene in cat['scenes']:
            name = scene['sceneName']
            if name in base:
                print(f"Warning: {model} duplicates scene {cat['categoryName']} - {name}")
                print("duplicate", name, "=", self.scene(scene))
            else:
                base[name] = self.scene(scene)
        return base
    
    def file(self, model: str, root: GoveeRoot):
        base: ConsolidateCategories = {}
        for cat in root['data']['categories']:
            name = cat['categoryName']
            if name in base:
                print(f"Warning: {model} duplicates category {name}")
                print("duplicate", name, "=", self.category(model, cat))
            else:
                base[name] = self.category(model, cat)
        return base
    
    def all(self, root: str):
        # Iterate over ./jsons/
        for file in os.listdir(root):
            name, ext = os.path.splitext(file)
            if ext != '.json':
                continue
            with open(f'{root}/{file}') as f:
                data: GoveeRoot = json.load(f)
            
            try:
                consolidate = self.file(name, data)
            except Exception as e:
                e.add_note(f'File: {file}')
                raise
            
            with open(f'scenes/{name}.json', 'w') as f:
                json.dump(consolidate, f)
    
    def summarize(self, data):
        for dev, cats in data.items():
            print(dev)
            for cname, cat in cats.items():
                print(f"  Category: {cname} {cat['id']}")
                for scene in cat['scenes']:
                    if scene['code']:
                        print(f"    Scene: {scene['name']} 0x{scene['code']:04X}")
                    else:
                        print(f"    Scene: {scene['name']}")
                    for effect in scene['effects']:
                        print(f"      Effect: 0x{effect['code']:04X} {effect['param']}")

def main():
    c = Consolidate()
    c.all("jsons")

if __name__ == '__main__':
    main()