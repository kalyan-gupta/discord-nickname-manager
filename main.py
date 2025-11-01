import os
import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore
import asyncio
import json
import logging
from flask import Flask
from threading import Thread

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Firebase
def initialize_firebase():
    try:
        # Get Firebase config from environment variable
        firebase_config_json = os.environ.get('FIREBASE_CONFIG')
        if firebase_config_json:
            cred_dict = json.loads(firebase_config_json)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            logger.info("✅ Firebase initialized successfully")
            return True
        else:
            logger.error("❌ FIREBASE_CONFIG not found in environment variables")
            return False
    except Exception as e:
        logger.error(f"❌ Failed to initialize Firebase: {e}")
        return False

if not firebase_admin._apps:
    initialize_firebase()

db = firestore.AsyncClient()

class NicknameGuardian:
    def __init__(self, bot):
        self.bot = bot
    
    async def get_bot_highest_role(self, guild):
        bot_member = guild.get_member(self.bot.user.id)
        return bot_member.top_role if bot_member else None
    
    async def can_manage_immunity(self, ctx):
        if ctx.author.id == ctx.guild.owner_id:
            return True
        bot_highest_role = await self.get_bot_highest_role(ctx.guild)
        if not bot_highest_role:
            return False
        author_highest_role = ctx.author.top_role
        return author_highest_role > bot_highest_role
    
    async def can_user_change_others_nicknames(self, user, guild):
        if user.id == guild.owner_id:
            return True
        if await self.is_user_immune(user, guild):
            return True
        return False
    
    async def initialize_user(self, user, guild):
        try:
            user_ref = db.collection('servers').document(str(guild.id)).collection('users').document(str(user.id))
            user_data = {
                'user_id': user.id,
                'guild_id': guild.id,
                'nickname': user.display_name,
                'last_updated': firestore.SERVER_TIMESTAMP,
                'is_self_change': True,
                'username': str(user)
            }
            await user_ref.set(user_data, merge=True)
        except Exception as e:
            logger.error(f"Error initializing user {user.id}: {e}")
    
    async def is_user_immune(self, user, guild):
        try:
            immune_roles_ref = db.collection('servers').document(str(guild.id)).collection('immune_roles')
            immune_roles_snapshot = await immune_roles_ref.get()
            immune_role_ids = [doc.id for doc in immune_roles_snapshot]
            
            if not immune_role_ids:
                return False
            
            user_role_ids = [str(role.id) for role in user.roles]
            return any(immune_role_id in user_role_ids for immune_role_id in immune_role_ids)
        except Exception as e:
            logger.error(f"Error checking user immunity {user.id}: {e}")
            return False
    
    async def update_nickname_record(self, user, guild, new_nickname, updated_by, is_self_change):
        try:
            user_ref = db.collection('servers').document(str(guild.id)).collection('users').document(str(user.id))
            user_data = {
                'nickname': new_nickname,
                'last_updated': firestore.SERVER_TIMESTAMP,
                'updated_by': updated_by,
                'is_self_change': is_self_change,
                'username': str(user)
            }
            await user_ref.set(user_data, merge=True)
        except Exception as e:
            logger.error(f"Error updating nickname record for user {user.id}: {e}")
    
    async def get_previous_nickname(self, user, guild):
        try:
            user_ref = db.collection('servers').document(str(guild.id)).collection('users').document(str(user.id))
            doc = await user_ref.get()
            return doc.get('nickname') if doc.exists else None
        except Exception as e:
            logger.error(f"Error getting previous nickname for user {user.id}: {e}")
            return None
    
    async def add_immune_role(self, role):
        try:
            role_ref = db.collection('servers').document(str(role.guild.id)).collection('immune_roles').document(str(role.id))
            await role_ref.set({
                'role_id': role.id,
                'role_name': role.name,
                'guild_id': role.guild.id,
                'added_at': firestore.SERVER_TIMESTAMP
            })
            logger.info(f"Added immune role: {role.name}")
        except Exception as e:
            logger.error(f"Error adding immune role {role.id}: {e}")
            raise
    
    async def remove_immune_role(self, role):
        try:
            role_ref = db.collection('servers').document(str(role.guild.id)).collection('immune_roles').document(str(role.id))
            await role_ref.delete()
            logger.info(f"Removed immune role: {role.name}")
        except Exception as e:
            logger.error(f"Error removing immune role {role.id}: {e}")
            raise
    
    async def get_immune_roles(self, guild):
        try:
            roles_ref = db.collection('servers').document(str(guild.id)).collection('immune_roles')
            snapshot = await roles_ref.get()
            immune_roles = []
            for doc in snapshot:
                role_data = doc.to_dict()
                role = guild.get_role(int(doc.id))
                if role:
                    immune_roles.append({
                        'role': role,
                        'data': role_data,
                        'member_count': len(role.members)
                    })
            return immune_roles
        except Exception as e:
            logger.error(f"Error getting immune roles for guild {guild.id}: {e}")
            return []

    async def handle_nickname_change(self, before, after):
        if before.display_name == after.display_name:
            return
        
        guild = after.guild
        await self.initialize_user(after, guild)
        
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id:
                    actor = entry.user
                    break
            else:
                actor = after
        except discord.Forbidden:
            actor = after
        
        is_self_change = actor.id == after.id
        await self.update_nickname_record(after, guild, after.display_name, actor.id, is_self_change)
        
        if not is_self_change and actor != guild.me:
            if await self.can_user_change_others_nicknames(actor, guild):
                return
            
            previous_nickname = await self.get_previous_nickname(after, guild)
            if previous_nickname and previous_nickname != after.display_name:
                try:
                    await after.edit(nick=previous_nickname)
                    logger.info(f"🛡️ Reverted nickname for {after.display_name} (changed by {actor.display_name})")
                    
                    # Send log message
                    channel = discord.utils.get(guild.text_channels, name="nickname-logs")
                    if channel:
                        embed = discord.Embed(
                            title="🛡️ Nickname Reverted",
                            color=0xff6b6b,
                            timestamp=discord.utils.utcnow()
                        )
                        embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
                        embed.add_field(name="Changed By", value=f"{actor.mention} (`{actor.id}`)", inline=False)
                        embed.add_field(name="Reason", value="User not authorized to change others' nicknames", inline=False)
                        embed.add_field(name="Reverted To", value=previous_nickname, inline=False)
                        await channel.send(embed=embed)
                except Exception as e:
                    logger.error(f"Error reverting nickname: {e}")

# Initialize bot
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
guardian = NicknameGuardian(bot)

@bot.event
async def on_ready():
    logger.info(f'✅ {bot.user} is online!')
    logger.info(f'📊 Connected to {len(bot.guilds)} guild(s)')
    for guild in bot.guilds:
        logger.info(f'   - {guild.name} (ID: {guild.id})')
        for member in guild.members:
            await guardian.initialize_user(member, guild)

@bot.event
async def on_member_update(before, after):
    if before.display_name != after.display_name:
        await guardian.handle_nickname_change(before, after)

@bot.command(name='immune_role')
async def immune_role(ctx, role: discord.Role):
    """Add a role to the immune list"""
    if not await guardian.can_manage_immunity(ctx):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need to be server owner or have a role higher than my highest role to use this command.",
            color=0xff6b6b
        )
        return await ctx.send(embed=embed)
    
    await guardian.add_immune_role(role)
    
    embed = discord.Embed(
        title="✅ Role Added to Immune List",
        description=f"All **current** members of {role.mention} can now change **other users'** nicknames.",
        color=0x51cf66
    )
    embed.add_field(name="Role Members", value=f"{len(role.members)} members", inline=True)
    embed.add_field(name="Note", value="All users can always change their **own** nicknames", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='unimmune_role')
async def unimmune_role(ctx, role: discord.Role):
    """Remove a role from the immune list"""
    if not await guardian.can_manage_immunity(ctx):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need to be server owner or have a role higher than my highest role to use this command.",
            color=0xff6b6b
        )
        return await ctx.send(embed=embed)
    
    await guardian.remove_immune_role(role)
    
    embed = discord.Embed(
        title="✅ Role Removed from Immune List",
        description=f"The role {role.mention} has been removed from the immune list.",
        color=0x51cf66
    )
    await ctx.send(embed=embed)

@bot.command(name='immune_roles')
async def immune_roles(ctx):
    """List all immune roles"""
    immune_roles_list = await guardian.get_immune_roles(ctx.guild)
    
    if not immune_roles_list:
        embed = discord.Embed(
            title="🛡️ Immune Roles",
            description="No immune roles configured. Only the server owner can change others' nicknames.",
            color=0x339af0
        )
    else:
        embed = discord.Embed(
            title="🛡️ Immune Roles",
            description="Members of these roles can change **other users'** nicknames:",
            color=0x339af0
        )
        
        for immune_role in immune_roles_list:
            role = immune_role['role']
            embed.add_field(
                name=f"{role.name}",
                value=f"Members: {immune_role['member_count']}",
                inline=True
            )
    
    embed.add_field(
        name="ℹ️ Note",
        value="**All users can always change their own nicknames**",
        inline=False
    )
    await ctx.send(embed=embed)

@bot.command(name='bot_status')
async def bot_status(ctx):
    """Check bot status"""
    bot_highest_role = await guardian.get_bot_highest_role(ctx.guild)
    immune_roles_list = await guardian.get_immune_roles(ctx.guild)
    
    embed = discord.Embed(
        title="🤖 Bot Status",
        color=0x339af0
    )
    
    embed.add_field(
        name="Bot's Highest Role",
        value=f"{bot_highest_role.mention if bot_highest_role else 'None'}",
        inline=True
    )
    
    embed.add_field(
        name="Immune Roles",
        value=f"{len(immune_roles_list)} configured",
        inline=True
    )
    
    embed.add_field(
        name="Your Permissions",
        value="✅ Can manage" if await guardian.can_manage_immunity(ctx) else "❌ Cannot manage",
        inline=True
    )
    
    await ctx.send(embed=embed)

@bot.command(name='help_guardian')
async def help_guardian(ctx):
    """Show help"""
    embed = discord.Embed(
        title="🛡️ Nickname Guardian Help",
        color=0x339af0
    )
    
    embed.add_field(
        name="Commands",
        value=(
            "`!immune_role @Role` - Add immune role\n"
            "`!unimmune_role @Role` - Remove immune role\n"
            "`!immune_roles` - List immune roles\n"
            "`!bot_status` - Check bot status\n"
            "`!help_guardian` - This help message"
        ),
        inline=False
    )
    
    embed.add_field(
        name="Rules",
        value=(
            "• Everyone can change **their own** nickname\n"
            "• Only server owner and immune roles can change **others'** nicknames\n"
            "• Only users with roles **above the bot** can manage immune roles"
        ),
        inline=False
    )
    
    await ctx.send(embed=embed)

# Flask server to keep repl alive
app = Flask('')

@app.route('/')
def home():
    return "🛡️ Nickname Guardian Bot is running!"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# Start the bot
if __name__ == "__main__":
    keep_alive()
    token = os.environ.get('DISCORD_TOKEN')
    if token:
        logger.info("🚀 Starting bot...")
        bot.run(token)
    else:
        logger.error("❌ DISCORD_TOKEN not found in environment variables")
