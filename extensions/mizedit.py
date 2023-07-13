import random
from pathlib import Path

import yaml

from core import Extension, utils, Server
from datetime import datetime
from typing import Optional


class MizEdit(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.presets = yaml.safe_load(Path("config/presets.yaml").read_text(encoding='utf-8'))

    @property
    def version(self) -> str:
        return "1.0.0"

    async def change_mizfile(self, server: Server, config: dict, presets: Optional[str] = None):
        now = datetime.now()
        if not presets:
            if isinstance(config['settings'], dict):
                for key, value in config['settings'].items():
                    if utils.is_in_timeframe(now, key):
                        presets = value
                        break
                if not presets:
                    # no preset found for the current time, so don't change anything
                    return
            elif isinstance(config['settings'], list):
                presets = random.choice(config['settings'])
        modifications = []
        for preset in [x.strip() for x in presets.split(',')]:
            if preset not in self.presets:
                self.log.error(f'Preset {preset} not found, ignored.')
                continue
            value = self.presets[preset]
            if isinstance(value, list):
                for inner_preset in value:
                    if inner_preset not in self.presets:
                        self.log.error(f'Preset {inner_preset} not found, ignored.')
                        continue
                    inner_value = self.presets[inner_preset]
                    modifications.append(inner_value)
            elif isinstance(value, dict):
                modifications.append(value)
            self.log.info(f"  => Preset {preset} applied.")
        await self.server.modifyMission(modifications)
        self.log.info(f"  => Mission modified.")

    async def beforeMissionLoad(self) -> bool:
        await self.change_mizfile(self.server, self.config)
        return True

    def is_installed(self) -> bool:
        return True
