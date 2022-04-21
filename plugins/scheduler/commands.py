import json
import psutil
import string
from core import Plugin, DCSServerBot, PluginRequiredError, utils, TEventListener, Status
from datetime import datetime, timedelta
from discord.ext import tasks, commands
from os import path
from typing import Type, Optional, List
from .listener import SchedulerListener


class Scheduler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_state.start()

    def cog_unload(self):
        self.check_state.cancel()
        super().cog_unload()

    # TODO: remove in a later version
    def migrate(self, filename: str) -> dict:
        # check all server configurations for possible restart settings
        locals = {
            'configs': []
        }
        for _, installation in utils.findDCSInstallations():
            if installation in self.config:
                config = self.config[installation]
                settings = {
                    'installation': installation,
                }
                if 'RESTART_METHOD' in self.config[installation]:
                    settings['restart'] = {
                        "method": config['RESTART_METHOD']
                    }
                    if 'RESTART_LOCAL_TIMES' in config:
                        settings['restart']['local_times'] = \
                            [x.strip() for x in config['RESTART_LOCAL_TIMES'].split(',')]
                    elif 'RESTART_MISSION_TIME' in config:
                        settings['restart']['mission_time'] = int(config['RESTART_MISSION_TIME'])
                    if 'RESTART_OPTIONS' in config:
                        if 'NOT_POPULATED' in config['RESTART_OPTIONS']:
                            settings['restart']['populated'] = False
                        if 'RESTART_SERVER' in config['RESTART_OPTIONS']:
                            settings['restart']['method'] = 'restart_with_shutdown'
                    if 'RESTART_WARN_TIMES' in config:
                        settings['warn'] = {
                            "times": [int(x) for x in config['RESTART_WARN_TIMES'].split(',')]
                        }
                        if 'RESTART_WARN_TEXT' in config:
                            settings['warn']['text'] = config['RESTART_WARN_TEXT']
                if 'AUTOSTART_DCS' in self.config[installation] and \
                        self.config.getboolean(installation, 'AUTOSTART_DCS') is True:
                    settings['schedule'] = {"00:00-23:59": "YYYYYYY"}
                if 'AUTOSTART_SRS' in self.config[installation] and \
                        self.config.getboolean(installation, 'AUTOSTART_SRS') is True:
                    settings['extensions'] = ["SRS"]
                locals['configs'].append(settings)
        with open(filename, 'w') as outfile:
            json.dump(locals, outfile, indent=2)
        self.log.info(f'  => Migrated data from dcsserverbot.ini into {filename}.\n     You can remove the '
                      f'AUTOSTART and RESTART options now from your dcsserverbot.ini.\n     Please check the newly '
                      f'created {filename} for any needed amendments.')
        return locals

    def read_locals(self):
        filename = f'./config/{self.plugin_name}.json'
        if not path.exists(filename):
            return self.migrate(filename)
        else:
            return super().read_locals()

    def get_config(self, server: dict) -> Optional[dict]:
        if self.plugin_name not in server:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server['installation'] == element['installation']) or \
                                ('server_name' in element and server['server_name'] == element['server_name']):
                            specific = element
                    else:
                        default = element
                if default and not specific:
                    server[self.plugin_name] = default
                elif specific and not default:
                    server[self.plugin_name] = specific
                elif default and specific:
                    merged = default.copy()
                    # specific settings will always overwrite default settings
                    for key, value in specific.items():
                        merged[key] = value
                    server[self.plugin_name] = merged
            else:
                return None
        return server[self.plugin_name] if self.plugin_name in server else None

    def check_server_state(self, server: dict, config: dict) -> Status:
        if 'schedule' in config:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) and utils.is_populated(self, server) else 0
            now = datetime.now()
            weekday = now.weekday()
            for period, daystate in config['schedule'].items():
                state = daystate[weekday]
                # check, if the server should be running
                if utils.is_in_timeframe(now, period) and state.upper() == 'Y' and server['status'] == Status.SHUTDOWN:
                    return Status.RUNNING
                elif utils.is_in_timeframe(now, period) and state.upper() == 'P' and server['status'] in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and not utils.is_populated(self, server):
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now + timedelta(seconds=restart_in), period) and state.upper() == 'N' and server['status'] == Status.RUNNING:
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now, period) and state.upper() == 'N' and server['status'] in [Status.PAUSED, Status.STOPPED]:
                    return Status.SHUTDOWN
        return server['status']

    def launch_extensions(self, server: dict, config: dict):
        for extension in config['extensions']:
            if extension == 'SRS' and not utils.check_srs(self, server):
                self.log.info(f"  => Launching DCS-SRS server \"{server['server_name']}\" by {string.capwords(self.plugin_name)} ...")
                utils.start_srs(self, server)

    async def launch(self, server: dict, config: dict):
        self.log.info(f"  => Launching DCS server \"{server['server_name']}\" by "
                      f"{string.capwords(self.plugin_name)} ...")
        await self.bot.audit(f"{string.capwords(self.plugin_name)} started DCS server", server=server)
        utils.start_dcs(self, server)
        if 'extensions' in config:
            self.launch_extensions(server, config)

    @staticmethod
    def get_warn_times(config: dict) -> List[int]:
        if 'warn' in config and 'times' in config['warn']:
            return config['warn']['times']
        return []

    def warn_users(self, server: dict, config: dict) -> int:
        restart_in = 0
        if 'warn' in config and utils.is_populated(self, server):
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) else 0
            warn_text = config['warn']['text'] if 'text' in config['warn'] \
                else '!!! Server will restart in {} seconds !!!'
            for warn_time in warn_times:
                self.loop.call_later(restart_in - warn_time, self.bot.sendtoDCS,
                                     server, {
                                        'command': 'sendPopupMessage',
                                        'message': warn_text.format(warn_time),
                                        'to': 'all',
                                        'time': self.config['BOT']['MESSAGE_TIMEOUT']
                                     })
        return restart_in

    def shutdown_extensions(self, server: dict, config: dict):
        for extension in config['extensions']:
            if extension == 'SRS' and utils.check_srs(self, server):
                self.log.info(f"  => Stopping DCS-SRS server \"{server['server_name']}\" by "
                              f"{string.capwords(self.plugin_name)} ...")
                utils.stop_srs(self, server)

    async def shutdown(self, server: dict, config: dict):
        # if we should not restart populated servers, wait for it to be unpopulated
        if 'populated' in config and config['populated'] is False and utils.is_populated(self, server):
            return
        elif 'restart_pending' not in server:
            server['restart_pending'] = True
            restart_in = self.warn_users(server, config)
            if restart_in > 0:
                self.log.info(f"  => DCS server \"{server['server_name']}\" will be stopped "
                              f"by {string.capwords(self.plugin_name)} in {restart_in} seconds ...")
                await self.bot.audit(f"{string.capwords(self.plugin_name)} stopping DCS server in {restart_in} seconds",
                                     server=server)
            else:
                self.log.info(
                    f"  => Stopping DCS server \"{server['server_name']}\" by {string.capwords(self.plugin_name)} ...")
                await self.bot.audit(f"{string.capwords(self.plugin_name)} stopped DCS server", server=server)
            self.loop.call_later(restart_in, self.bot.sendtoBot,
                                 {"command": "onMissionEnd", "server_name": server['server_name']})
            self.loop.call_later(restart_in + 1, self.bot.sendtoBot,
                                 {"command": "onShutdown", "server_name": server['server_name']})
            self.loop.call_later(restart_in + 2, utils.stop_dcs, self, server)
            if 'extensions' in config:
                self.loop.call_later(restart_in, self.shutdown_extensions, server, config)

    def restart_mission(self, server: dict, config: dict):
        # check if the mission is still populated
        if 'populated' in config['restart'] and config['restart']['populated'] is False and utils.is_populated(self, server):
            return
        elif 'restart_pending' not in server:
            server['restart_pending'] = True
            method = config['restart']['method']
            restart_time = self.warn_users(server, config)
            if method == 'restart_with_shutdown':
                self.loop.call_later(restart_time, self.bot.sendtoBot,
                                     {"command": "onMissionEnd", "server_name": server['server_name']})
                self.loop.call_later(restart_time, utils.stop_dcs, self, server)
                self.loop.call_later(restart_time + 30, utils.start_dcs, self, server)
            elif method == 'restart':
                self.loop.call_later(restart_time, self.bot.sendtoBot,
                                     {"command": "onMissionEnd", "server_name": server['server_name']})
                self.loop.call_later(restart_time, self.bot.sendtoDCS, server, {"command": "restartMission"})
            elif method == 'rotate':
                self.loop.call_later(restart_time, self.bot.sendtoBot,
                                     {"command": "onMissionEnd", "server_name": server['server_name']})
                self.loop.call_later(restart_time, self.bot.sendtoDCS, server, {"command": "startNextMission"})

    def check_mission_state(self, server: dict, config: dict):
        if 'restart' in config:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) and utils.is_populated(self, server) else 0
            if 'mission_time' in config['restart'] and \
                    (server['mission_time'] - restart_in) >= (int(config['restart']['mission_time']) * 60):
                self.restart_mission(server, config)
            elif 'local_times' in config['restart']:
                now = datetime.now() + timedelta(seconds=restart_in)
                for t in config['restart']['local_times']:
                    if utils.is_in_timeframe(now, t):
                        self.restart_mission(server, config)

    def check_affinity(self, server, config):
        if 'PID' not in server:
            p = utils.find_process('DCS.exe', server['installation'])
            server['PID'] = p.pid
        pid = server['PID']
        ps = psutil.Process(pid)
        ps.cpu_affinity(config['affinity'])

    @tasks.loop(minutes=1.0)
    async def check_state(self):
        # check all servers
        for server_name, server in self.globals.items():
            # only care about servers that are not in the startup phase
            if server['status'] in [Status.UNREGISTERED, Status.LOADING] or \
                    'maintenance' in server or 'restart_pending' in server:
                continue
            config = self.get_config(server)
            # if no config is defined for this server, ignore it
            if config:
                if server['status'] == Status.RUNNING and 'affinity' in config:
                    self.check_affinity(server, config)
                target_state = self.check_server_state(server, config)
                if server['status'] != target_state:
                    if target_state == Status.RUNNING:
                        await self.launch(server, config)
                    elif target_state == Status.SHUTDOWN:
                        await self.shutdown(server, config)
                elif server['status'] in [Status.RUNNING, Status.PAUSED]:
                    self.check_mission_state(server, config)

    @check_state.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @commands.command(description='Clears the servers maintenance flag')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def clear(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if 'maintenance' in server:
                del server['maintenance']
                await ctx.send(f"Maintenance mode cleared for server {server['server_name']}.\n"
                               f"The {string.capwords(self.plugin_name)} will take over the state handling now.")
                await self.bot.audit("cleared maintenance flag", user=ctx.message.author, server=server)
            else:
                await ctx.send(f"Server {server['server_name']} is not in maintenance mode.")


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(Scheduler(bot, SchedulerListener))
