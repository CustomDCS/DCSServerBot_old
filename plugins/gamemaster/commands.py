import dateparser
import discord
import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, utils, Report
from core.const import Status
from discord.ext import commands
from typing import Optional
from .listener import GameMasterEventListener


class GameMasterAgent(Plugin):

    def rename(self, old_name: str, new_name: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE campaigns SET server_name = %s WHERE server_name = %s', (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_message(self, message):
        # ignore bot messages
        if message.author.bot:
            return
        for server in self.globals.values():
            if server['status'] != Status.RUNNING:
                continue
            if self.config[server['installation']]['COALITIONS']:
                sides = utils.get_sides(message, server)
                if 'Blue' in sides and 'coalition_blue_channel' in server and \
                        server["coalition_blue_channel"] == str(message.channel.id):
                    # TODO: ignore messages for now, as DCS does not understand the coalitions yet
                    # self.bot.sendtoDCS(server, {
                    #    "command": "sendChatMessage",
                    #    "message": message.content,
                    #    "from": message.author.display_name,
                    #    "to": const.SIDE_BLUE * -1
                    # })
                    pass
                elif 'Red' in sides and 'coalition_red_channel' in server and \
                        server["coalition_red_channel"] == str(message.channel.id):
                    # TODO:  ignore messages for now, as DCS does not understand the coalitions yet
                    # self.bot.sendtoDCS(server, {
                    #    "command": "sendChatMessage",
                    #    "message": message.content,
                    #    "from": message.author.display_name,
                    #    "to": const.SIDE_BLUE * -1
                    # })
                    pass
            if 'chat_channel' in server and server["chat_channel"] == str(message.channel.id):
                if message.content.startswith(self.config['BOT']['COMMAND_PREFIX']) is False:
                    self.bot.sendtoDCS(server, {
                        "command": "sendChatMessage",
                        "message": message.content,
                        "from": message.author.display_name
                    })

    @commands.command(description='Send a chat message to a running DCS instance', usage='<message>', hidden=True)
    @utils.has_role('DCS')
    @commands.guild_only()
    async def chat(self, ctx, *args):
        server = await utils.get_server(self, ctx)
        if server and server['status'] == Status.RUNNING:
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "channel": ctx.channel.id,
                "message": ' '.join(args),
                "from": ctx.message.author.display_name
            })

    @commands.command(description='Sends a popup to a coalition', usage='<coal.|user> [time] <msg>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def popup(self, ctx, to, *args):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] != Status.RUNNING:
                await ctx.send(f"Mission is {server['status'].name.lower()}, message discarded.")
                return
            if len(args) > 0:
                if args[0].isnumeric():
                    time = int(args[0])
                    i = 1
                else:
                    time = self.config['BOT']['MESSAGE_TIMEOUT']
                    i = 0
                if to not in ['all', 'red', 'blue']:
                    player = utils.get_player(self, server['server_name'], name=to, active=True)
                    if player and 'slot' in player and len(player['slot']) > 0:
                        to = player['slot']
                    else:
                        await ctx.send(f"Can't find player {to} or player is not in an active unit.")
                        return
                self.bot.sendtoDCS(server, {
                    "command": "sendPopupMessage",
                    "channel": ctx.channel.id,
                    "message": ' '.join(args[i:]),
                    "time": time,
                    "from": ctx.message.author.display_name,
                    "to": to.lower()
                })
                await ctx.send('Message sent.')
            else:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}popup all|red|blue|user [time] <message>")

    @commands.command(description='Set or clear a flag inside the mission', usage='<flag> [value]')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def flag(self, ctx, flag: int, value: int = None):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            if value is not None:
                self.bot.sendtoDCS(server, {
                    "command": "setFlag",
                    "channel": ctx.channel.id,
                    "flag": flag,
                    "value": value
                })
                await ctx.send(f"Flag {flag} set to {value}.")
            else:
                data = await self.bot.sendtoDCSSync(server, {"command": "getFlag", "flag": flag})
                await ctx.send(f"Flag {flag} has value {data['value']}.")
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, can't set/get flag.")

    @commands.command(description='Calls any function inside the mission', usage='<script>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def do_script(self, ctx, *script):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            self.bot.sendtoDCS(server, {
                "command": "do_script",
                "script": ' '.join(script)
            })
            await ctx.send('Command sent.')
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, command discarded.")

    @commands.command(description='Loads a lua file into the mission', usage='<file>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def do_script_file(self, ctx, filename):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            self.bot.sendtoDCS(server, {
                "command": "do_script_file",
                "file": filename.replace('\\', '/')
            })
            await ctx.send('Command sent.')
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, command discarded.")

    @staticmethod
    def format_campaigns(data, marker, marker_emoji):
        embed = discord.Embed(title="Active & Upcoming Campaigns", color=discord.Color.blue())
        names = start_times = end_times = ''
        for i in range(0, len(data)):
            names += data[i]['name'] + '\n'
            start_times += f"{data[i]['start']:%y-%m-%d %H:%M:%S}\n"
            end_times += f"{data[i]['stop']:%y-%m-%d %H:%M:%S}\n" if data[i]['stop'] else '-\n'
        embed.add_field(name='Name', value=names)
        embed.add_field(name='Start', value=start_times)
        embed.add_field(name='End', value=end_times)
        embed.set_footer(text='Press a number to display details about that specific campaign.')
        return embed

    @commands.command(description='Campaign Management',
                      usage='<add|start|stop|delete|list>',
                      aliases=['season'])
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def campaign(self, ctx, command: Optional[str], name: Optional[str], start_time: Optional[str], end_time: Optional[str]):
        server = await utils.get_server(self, ctx)
        if not command:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                    cursor.execute('SELECT * FROM campaigns WHERE NOW() BETWEEN start AND COALESCE(stop, NOW())')
                    if cursor.rowcount > 0:
                        report = Report(self.bot, self.plugin_name, 'campaign.json')
                        env = await report.render(campaign=dict(cursor.fetchone()), title='Active Campaign')
                        await ctx.send(embed=env.embed)
                    else:
                        await ctx.send('No running campaign found.')
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)
        elif command.lower() == 'add':
            if not name:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}.campaign add <name> <start> [stop]")
                return
            if not start_time:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}.campaign add <name> <start> [stop]")
                return
            description = await utils.input_value(self, ctx, 'Please enter a short description for this campaign '
                                                             '(. for none):')
            try:
                self.eventlistener.campaign('add', server, name, description,
                                            dateparser.parse(start_time, settings={'TIMEZONE': 'UTC'}) if start_time else None,
                                            dateparser.parse(end_time, settings={'TIMEZONE': 'UTC'}) if end_time else None)
                await ctx.send(f"Campaign {name} added on server {server['server_name']}")
            except psycopg2.errors.ExclusionViolation:
                await ctx.send(f"A campaign is already configured for this timeframe on server {server['server_name']}!")
            except psycopg2.errors.UniqueViolation:
                await ctx.send(f"A campaign with this name already exists on server {server['server_name']}!")
        elif command.lower() == 'start':
            try:
                if not name:
                    await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}.campaign start <name>")
                    return
                self.eventlistener.campaign('start', server, name)
                await ctx.send(f"Campaign {name} started on server {server['server_name']}")
            except psycopg2.errors.ExclusionViolation:
                await ctx.send(f"There is a campaign already running on server {server['server_name']}!")
            except psycopg2.errors.UniqueViolation:
                await ctx.send(f"A campaign with this name already exists on server {server['server_name']}!")
        elif command.lower() == 'stop':
            warn_text = f"Do you want to stop the running campaign on server \"{server['server_name']}\"?"
            if await utils.yn_question(self, ctx, warn_text) is True:
                self.eventlistener.campaign('stop', server)
                await ctx.send(f"Campaign stopped.")
            else:
                await ctx.send('Aborted.')
        elif command.lower() in ['del', 'delete']:
            if name:
                warn_text = f"Do you want to delete campaign \"{name}\" on server \"{server['server_name']}\"?"
            else:
                warn_text = f"Do you want to delete the current running campaign on server \"{server['server_name']}\"?"
            if await utils.yn_question(self, ctx, warn_text) is True:
                self.eventlistener.campaign('delete', server, name)
                await ctx.send(f"Campaign deleted.")
            else:
                await ctx.send('Aborted.')
        elif command.lower() == 'list':
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                    if name != "-all":
                        cursor.execute('SELECT * FROM campaigns WHERE COALESCE(stop, NOW()) >= NOW() ORDER BY start '
                                       'DESC')
                    else:
                        cursor.execute('SELECT * FROM campaigns ORDER BY start DESC')
                    if cursor.rowcount > 0:
                        campaigns = [dict(row) for row in cursor.fetchall()]
                        n = await utils.selection_list(self, ctx, campaigns, self.format_campaigns)
                        if n != -1:
                            report = Report(self.bot, self.plugin_name, 'campaign.json')
                            env = await report.render(campaign=campaigns[n], title='Campaign Overview')
                            await ctx.send(embed=env.embed)
                    else:
                        await ctx.send('No upcoming campaigns found.')
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)


class GameMasterMaster(GameMasterAgent):

    @commands.command(description='Join a coalition (red / blue)', usage='[red | blue]')
    @utils.has_role('DCS')
    @utils.has_not_roles(['Coalition Blue', 'Coalition Red', 'GameMaster'])
    @commands.guild_only()
    async def join(self, ctx, coalition: str):
        member = ctx.message.author
        roles = {
            "red": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Red']),
            "blue": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Blue'])
        }
        if coalition.casefold() not in roles.keys():
            await ctx.send('Usage: {}join [{}]'.format(self.config['BOT']['COMMAND_PREFIX'], '|'.join(roles.keys())))
            return
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # we don't care about coalitions if they left longer than one day before
                cursor.execute("SELECT coalition FROM players WHERE discord_id = %s AND coalition_leave > (NOW() - "
                               "interval %s)", (member.id, self.config['BOT']['COALITION_LOCK_TIME']))
                if cursor.rowcount == 1:
                    if cursor.fetchone()[0] != coalition.casefold():
                        await ctx.send(f"You can't join the {coalition} coalition in-between "
                                       f"{self.config['BOT']['COALITION_LOCK_TIME']} of leaving a coalition.")
                        await self.bot.audit(f'tried to join a new coalition in-between the time limit.', user=member)
                        return
                await member.add_roles(roles[coalition.lower()])
                cursor.execute('UPDATE players SET coalition = %s WHERE discord_id = %s', (coalition, member.id))
                await ctx.send(f'Welcome to the {coalition} side!')
                conn.commit()
        except discord.Forbidden:
            await ctx.send("I can't add you to this coalition. Please contact an Admin.")
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Leave your current coalition')
    @utils.has_roles(['Coalition Blue', 'Coalition Red'])
    @commands.guild_only()
    async def leave(self, ctx):
        member = ctx.message.author
        roles = {
            "red": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Red']),
            "blue": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Blue'])
        }
        for key, value in roles.items():
            if value in member.roles:
                conn = self.bot.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('UPDATE players SET coalition = NULL, coalition_leave = NOW() WHERE discord_id '
                                       '= %s', (member.id,))
                    conn.commit()
                    await member.remove_roles(value)
                    await ctx.send(f"You've left the {key} coalition!")
                    return
                except discord.Forbidden:
                    await ctx.send("I can't remove you from this coalition. Please contact an Admin.")
                    await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
                except (Exception, psycopg2.DatabaseError) as error:
                    self.bot.log.exception(error)
                    conn.rollback()
                finally:
                    self.bot.pool.putconn(conn)


def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(GameMasterMaster(bot, GameMasterEventListener))
    else:
        bot.add_cog(GameMasterAgent(bot, GameMasterEventListener))
