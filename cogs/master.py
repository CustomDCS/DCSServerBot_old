# master.py
import discord
import psycopg2
import psycopg2.extras
import utils
from cogs.agent import Agent
from contextlib import closing
from discord.ext import commands


class Master(Agent):

    def __init__(self, bot):
        super().__init__(bot)

    @commands.command(description='Bans a user by ucid or discord id', usage='<member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def ban(self, ctx, user, *args):
        if len(args) > 0:
            reason = ' '.join(args)
        else:
            reason = 'n/a'
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if user.startswith('<'):
                    discord_id = user.replace('<@!', '').replace('>', '')
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (discord_id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # ban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                                   (ucid, ctx.message.author.display_name, reason))
                conn.commit()
                await super().ban(self, ctx, user, *args)
            await ctx.send('Player {} banned.'.format(user))
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unban(self, ctx, user):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if user.startswith('<'):
                    discord_id = user.replace('<@!', '').replace('>', '')
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (discord_id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # unban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    cursor.execute('DELETE FROM bans WHERE ucid = %s', (ucid, ))
                conn.commit()
                await super().unban(self, ctx, user)
            await ctx.send('Player {} unbanned.'.format(user))
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Shows active bans')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def bans(self, ctx):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(
                    'SELECT p.ucid, p.discord_id, b.banned_by, b.reason FROM players p, bans b WHERE p.ucid = b.ucid')
                rows = list(cursor.fetchall())
                if rows is not None and len(rows) > 0:
                    embed = discord.Embed(title='List of Bans', color=discord.Color.blue())
                    ucids = discord_names = banned_by = ''
                    for ban in rows:
                        if ban['discord_id'] != -1:
                            user = await self.bot.fetch_user(ban['discord_id'])
                        else:
                            user = None
                        discord_names += (user.name if user else '<unknown>') + '\n'
                        ucids += ban['ucid'] + '\n'
                        banned_by += ban['banned_by'] + '\n'
                    embed.add_field(name='UCID', value=ucids)
                    embed.add_field(name='Name', value=discord_names)
                    embed.add_field(name='Banned by', value=banned_by)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send('No players are banned at the moment.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if self.bot.config.getboolean('BOT', 'AUTOBAN') is True:
            self.bot.log.debug('Member {} has left guild {} - ban them on DCS servers and delete their stats.'.format(member.display_name, member.guild.name))
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('INSERT INTO bans SELECT ucid, \'DCSServerBot\', \'Player left guild.\' FROM '
                                   'players WHERE discord_id = %s', (member.id, ))
                    cursor.execute('DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE '
                                   'discord_id = %s)', (member.id, ))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            finally:
                self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if self.bot.config.getboolean('BOT', 'AUTOBAN') is True:
            self.bot.log.debug(
                'Member {} has joined guild {} - remove possible bans from DCS servers.'.format(member.display_name, member.guild.name))
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute(
                        'DELETE FROM bans WHERE ucid IN (SELECT ucid FROM players WHERE discord_id = %s)', (member.id, ))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            finally:
                self.bot.pool.putconn(conn)
            self.updateBans()


def setup(bot):
    bot.add_cog(Master(bot))
