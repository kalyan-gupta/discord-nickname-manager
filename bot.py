import os
import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore
import json
import logging
from flask import Flask
from threading import Thread

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
firebase_project_id = None
db = None

def initialize_firebase():
    global firebase_project_id, db
    try:
        # Get Firebase config from environment variable
        firebase_config_json = os.environ.get('FIREBASE_CONFIG')
        
        if not firebase_config_json:
            logger.error("❌ FIREBASE_CONFIG environment variable not set")
            logger.info("💡 Please set FIREBASE_CONFIG with your service account JSON")
            return False
            
        logger.info("📁 Found FIREBASE_CONFIG, parsing...")
        
        # Parse the JSON string
        service_account_info = json.loads(firebase_config_json)
        
        # Extract project_id from service account
        firebase_project_id = service_account_info.get('project_id')
        if not firebase_project_id:
            logger.error("❌ project_id not found in FIREBASE_CONFIG")
            return False
        
        # Initialize Firebase with the service account
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        
        # Initialize Firestore
        db = firestore.client()
        
        logger.info(f"✅ Firebase initialized successfully with project: {firebase_project_id}")
        return True
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ Invalid JSON in FIREBASE_CONFIG: {e}")
        return False
    except ValueError as e:
        logger.error(f"❌ Invalid service account data: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Firebase initialization failed: {e}")
        return False

# Initialize Firebase
if not firebase_admin._apps:
    if not initialize_firebase():
        logger.error("❌ Failed to initialize Firebase. Bot will start but database features won't work.")
else:
    # If Firebase is already initialized, set up db
    try:
        db = firestore.client()
        logger.info("✅ Firestore client ready")
    except Exception as e:
        logger.error(f"❌ Failed to create Firestore client: {e}")
        db = None

class NicknameGuardian:
    def __init__(self, bot):
        self.bot = bot
        logger.info("🛡️ Nickname Guardian initialized")
    
    async def is_firebase_ready(self):
        """Check if Firebase is ready"""
        return db is not None
    
    async def get_bot_highest_role(self, guild):
        """Get the highest role of the bot in the guild"""
        bot_member = guild.get_member(self.bot.user.id)
        return bot_member.top_role if bot_member else None
    
    async def can_manage_immunity(self, ctx):
        """Check if user can manage immunity roles (has role higher than bot's highest role OR is server owner)"""
        if ctx.author.id == ctx.guild.owner_id:
            return True
            
        bot_highest_role = await self.get_bot_highest_role(ctx.guild)
        if not bot_highest_role:
            return False
            
        author_highest_role = ctx.author.top_role
        return author_highest_role > bot_highest_role
    
    async def can_user_change_others_nicknames(self, user, guild):
        """Check if user can change OTHER PEOPLE'S nicknames (is server owner OR has immune role)"""
        # Server owner can always change nicknames
        if user.id == guild.owner_id:
            return True
            
        # Users with immune roles can change nicknames
        if await self.is_user_immune(user, guild):
            return True
            
        return False
    
    async def initialize_user(self, user, guild):
        """Initialize user in Firestore with current nickname"""
        if not await self.is_firebase_ready():
            return
            
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
            
            user_ref.set(user_data)
            logger.debug(f"✅ Initialized user: {user.display_name}")
        except Exception as e:
            logger.error(f"❌ Error initializing user: {e}")
    
    async def is_user_immune(self, user, guild):
        """Check if user has ANY of the immune roles RIGHT NOW"""
        if not await self.is_firebase_ready():
            return False
            
        try:
            # Get all immune roles from database
            immune_roles_ref = db.collection('servers').document(str(guild.id)).collection('immune_roles')
            immune_roles_snapshot = immune_roles_ref.get()
            
            immune_role_ids = [doc.id for doc in immune_roles_snapshot]
            
            if not immune_role_ids:
                return False  # No immune roles configured
            
            # Check if user currently has any of the immune roles
            user_role_ids = [str(role.id) for role in user.roles]
            has_immunity = any(immune_role_id in user_role_ids for immune_role_id in immune_role_ids)
            logger.debug(f"🛡️ Immunity check for {user.name}: {has_immunity}")
            return has_immunity
        except Exception as e:
            logger.error(f"❌ Error checking immunity: {e}")
            return False
    
    async def update_nickname_record(self, user, guild, new_nickname, updated_by, is_self_change):
        """Update nickname record in Firestore"""
        if not await self.is_firebase_ready():
            return
            
        try:
            user_ref = db.collection('servers').document(str(guild.id)).collection('users').document(str(user.id))
            
            user_data = {
                'nickname': new_nickname,
                'last_updated': firestore.SERVER_TIMESTAMP,
                'updated_by': updated_by,
                'is_self_change': is_self_change,
                'username': str(user)
            }
            
            user_ref.set(user_data, merge=True)
            logger.debug(f"📝 Updated nickname record for {user.display_name}")
        except Exception as e:
            logger.error(f"❌ Error updating nickname record: {e}")
    
    async def get_previous_nickname(self, user, guild):
        """Get the previous legitimate nickname"""
        if not await self.is_firebase_ready():
            return None
            
        try:
            user_ref = db.collection('servers').document(str(guild.id)).collection('users').document(str(user.id))
            doc = user_ref.get()
            
            if doc.exists:
                return doc.get('nickname')
            else:
                await self.initialize_user(user, guild)
            return user.display_name
        except Exception as e:
            logger.error(f"❌ Error getting previous nickname: {e}")
            return None

    # Role-based immunity management
    async def add_immune_role(self, role):
        """Add role to immune list"""
        if not await self.is_firebase_ready():
            return False
            
        try:
            role_ref = db.collection('servers').document(str(role.guild.id)).collection('immune_roles').document(str(role.id))
            role_ref.set({
                'role_id': role.id,
                'role_name': role.name,
                'guild_id': role.guild.id,
                'added_at': firestore.SERVER_TIMESTAMP
            })
            logger.info(f"✅ Added immune role: {role.name}")
            return True
        except Exception as e:
            logger.error(f"❌ Error adding immune role: {e}")
            return False
    
    async def remove_immune_role(self, role):
        """Remove role from immune list"""
        if not await self.is_firebase_ready():
            return False
            
        try:
            role_ref = db.collection('servers').document(str(role.guild.id)).collection('immune_roles').document(str(role.id))
            role_ref.delete()
            logger.info(f"✅ Removed immune role: {role.name}")
            return True
        except Exception as e:
            logger.error(f"❌ Error removing immune role: {e}")
            return False
    
    async def get_immune_roles(self, guild):
        """Get all immune roles for a guild"""
        if not await self.is_firebase_ready():
            return []
            
        try:
            roles_ref = db.collection('servers').document(str(guild.id)).collection('immune_roles')
            snapshot = roles_ref.get()
            
            immune_roles = []
            for doc in snapshot:
                role_data = doc.to_dict()
                role = guild.get_role(int(doc.id))
                if role:  # Only include roles that still exist
                    immune_roles.append({
                        'role': role,
                        'data': role_data,
                        'member_count': len(role.members)
                    })
            return immune_roles
        except Exception as e:
            logger.error(f"❌ Error getting immune roles: {e}")
            return []
    
    async def handle_nickname_change(self, before, after):
        """Handle nickname changes and revert unauthorized ones"""
        # Skip if no nickname change
        if before.display_name == after.display_name:
            return
        guild = after.guild
        
        try:
            # Get audit log to see who made the change
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id:
                    actor = entry.user
                    break
            else:
                actor = after  # Assume self-change if no audit log entry
        except discord.Forbidden:
            # Fallback if bot can't read audit logs
            actor = after
        
        # Check if it's a self-change (user changing their own nickname)
        is_self_change = actor.id == after.id
        
        if(is_self_change):
            # Update the record
            await self.update_nickname_record(after, guild, after.display_name, actor.id, is_self_change)
        
        # ONLY intervene if it's NOT a self-change AND actor is not authorized to change others' nicknames
        if not is_self_change and actor != guild.me:
            if await self.can_user_change_others_nicknames(actor, guild):
                return  # Authorized users can change others' nicknames
            
            previous_nickname = await self.get_previous_nickname(after, guild)
            
            if previous_nickname and previous_nickname != after.display_name:
                try:
                    # Revert the nickname
                    await after.edit(nick=previous_nickname)
                    logger.info(f"🛡️ Reverted nickname for {after.display_name} (changed by {actor.display_name})")
                    
                    # Log the action
                    channel = discord.utils.get(guild.text_channels, name="audit-log")
                    if channel:
                        embed = discord.Embed(
                            title="Nickname Reverted",
                            color=discord.Color.orange(),
                            timestamp=discord.utils.utcnow()
                        )
                        embed.add_field(name="User", value=f"{after.mention} ({after.id})", inline=False)
                        embed.add_field(name="Changed By", value=f"{actor.mention} ({actor.id})", inline=False)
                        embed.add_field(name="Reason", value="User not authorized to change others' nicknames", inline=False)
                        embed.add_field(name="Attempted Nickname", value=after.display_name, inline=False)
                        embed.add_field(name="Reverted To", value=previous_nickname, inline=False)
                        await channel.send(embed=embed)
                        
                except discord.Forbidden:
                    logger.error(f"❌ Missing permissions to change nickname for {after.display_name}")
                except Exception as e:
                    logger.error(f"❌ Error reverting nickname: {e}")
        else:
            # This is a self-change or bot action, always allow it
            logger.info(f"✅ Allowed nickname change: {before.display_name} -> {after.display_name} (self-change: {is_self_change})")

# Initialize bot
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
guardian = NicknameGuardian(bot)

@bot.event
async def on_ready():
    logger.info(f'✅ {bot.user} is online!')
    logger.info(f'📊 Connected to {len(bot.guilds)} guild(s)')
    
    # Initialize all current members
    for guild in bot.guilds:
        for member in guild.members:
            await guardian.initialize_user(member, guild)
    
    # Test Firebase connection
    if await guardian.is_firebase_ready():
        logger.info("✅ Firebase connection verified")
    else:
        logger.warning("⚠️ Firebase not connected - immune role features disabled")

@bot.event
async def on_member_update(before, after):
    """Handle nickname changes"""
    logger.info("before display_name %s",before.display_name)
    logger.info("after display_name %s",after.display_name)
    if before.display_name != after.display_name:
        await guardian.handle_nickname_change(before, after)

# Immunity Management Commands - Only usable by users with role higher than bot's highest role OR server owner
@bot.command(name='immune_role')
async def immune_role(ctx, role: discord.Role):
    """Add a role to the immune list (All current members of this role can change OTHERS' nicknames)"""
    if not await guardian.can_manage_immunity(ctx):
        embed = discord.Embed(
            title="Permission Denied",
            description="You need to be server owner or have a role higher than my highest role to use this command.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed)
    
    # ANY role can be added to immunity list - no restrictions
    success = await guardian.add_immune_role(role)
    
    if success:
        embed = discord.Embed(
            title="Role Added to Immune List",
            description=f"All **current** members of {role.mention} can now change **other users'** nicknames.",
            color=discord.Color.green()
        )
        embed.add_field(name="Role Members", value=f"{len(role.members)} members have this role", inline=True)
        embed.add_field(name="Role Position", value=f"Position: {role.position}", inline=True)
        embed.add_field(name="Note", value="All users can always change their **own** nicknames", inline=False)
        embed.set_footer(text="Immunity is checked in real-time. Members lose immunity immediately if they lose this role.")
    else:
        embed = discord.Embed(
            title="Error",
            description="Failed to add role to immune list. Please check Firebase configuration.",
            color=discord.Color.red()
        )
    
    await ctx.send(embed=embed)

@bot.command(name='unimmune_role')
async def unimmune_role(ctx, role: discord.Role):
    """Remove a role from the immune list"""
    if not await guardian.can_manage_immunity(ctx):
        embed = discord.Embed(
            title="Permission Denied",
            description="You need to be server owner or have a role higher than my highest role to use this command.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed)
    
    success = await guardian.remove_immune_role(role)
    
    if success:
        embed = discord.Embed(
            title="Role Removed from Immune List",
            description=f"The role {role.mention} has been removed from the immune list.",
            color=discord.Color.green()
        )
        embed.set_footer(text="All members immediately lost the ability to change others' nicknames through this role.")
    else:
        embed = discord.Embed(
            title="Error",
            description="Failed to remove role from immune list. Please check Firebase configuration.",
            color=discord.Color.red()
        )
    
    await ctx.send(embed=embed)

@bot.command(name='immune_roles')
async def immune_roles(ctx):
    """List all immune roles and their current members"""
    immune_roles_list = await guardian.get_immune_roles(ctx.guild)
    
    if not immune_roles_list:
        embed = discord.Embed(
            title="Immune Roles",
            description="No immune roles configured. Only the server owner can change others' nicknames.\n\n**Note:** All users can always change their own nicknames.",
            color=discord.Color.blue()
        )
    else:
        embed = discord.Embed(
            title="Immune Roles",
            description="Members of these roles can change **other users'** nicknames:",
            color=discord.Color.blue()
        )
        
        for immune_role in immune_roles_list:
            role = immune_role['role']
            member_count = immune_role['member_count']
            
            # List first 5 members (to avoid embed field limits)
            member_list = [member.mention for member in role.members[:5]]
            member_text = "\n".join(member_list) if member_list else "No members"
            
            if len(role.members) > 5:
                member_text += f"\n...and {len(role.members) - 5} more"
            
            embed.add_field(
                name=f"{role.name} ({member_count} members)",
                value=member_text,
                inline=True
            )
        
        embed.add_field(
            name="ℹ️ Important Note",
            value="**All users can always change their own nicknames.**\nThis only affects changing **other people's** nicknames.",
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='check_permissions')
async def check_permissions(ctx, member: discord.Member = None):
    """Check what permissions a member has"""
    if member is None:
        member = ctx.author
    
    can_manage_immunity = await guardian.can_manage_immunity(ctx)
    can_change_others_nicknames = await guardian.can_user_change_others_nicknames(member, ctx.guild)
    is_immune = await guardian.is_user_immune(member, ctx.guild)
    bot_highest_role = await guardian.get_bot_highest_role(ctx.guild)
    
    embed = discord.Embed(
        title=f"Permission Check for {member.display_name}",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Can Manage Immune Roles",
        value="✅ Yes" if can_manage_immunity else "❌ No",
        inline=True
    )
    
    embed.add_field(
        name="Can Change OTHERS' Nicknames",
        value="✅ Yes" if can_change_others_nicknames else "❌ No",
        inline=True
    )
    
    embed.add_field(
        name="Can Change OWN Nickname",
        value="✅ Always Allowed",
        inline=True
    )
    
    embed.add_field(
        name="Has Immune Role",
        value="✅ Yes" if is_immune else "❌ No",
        inline=True
    )
    
    embed.add_field(
        name="Is Server Owner",
        value="✅ Yes" if member.id == ctx.guild.owner_id else "❌ No",
        inline=True
    )
    
    # Explain permissions clearly
    if member.id == ctx.guild.owner_id:
        others_reason = "Server Owner"
    elif is_immune:
        others_reason = "Immune Role membership"
    else:
        others_reason = "No permissions"
    
    embed.add_field(
        name="Others' Nickname Change Permission",
        value=others_reason,
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command(name='rules')
async def rules(ctx):
    """Show the complete rules of the nickname system"""
    embed = discord.Embed(
        title="Nickname System Rules",
        description="Understanding how nickname changes work:",
        color=discord.Color.green()
    )
    
    embed.add_field(
        name="✅ Always Allowed",
        value="• **Changing your own nickname** - Every user can always change their own nickname",
        inline=False
    )
    
    embed.add_field(
        name="🛡️ Who Can Change OTHERS' Nicknames",
        value="• Server Owner\n• Users with **immune role**",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Who Can Manage Immune Roles",
        value="• Server Owner\n• Users with any role **higher than** bot's highest role",
        inline=False
    )
    
    embed.add_field(
        name="🔓 Role Restrictions",
        value="• **Any role** can be added to immunity list\n• No position restrictions",
        inline=False
    )
    
    embed.add_field(
        name="📝 Available Commands",
        value="• `!immune_role @Role` - Add role to immune list\n• `!unimmune_role @Role` - Remove role\n• `!immune_roles` - List immune roles\n• `!check_permissions @User` - Check permissions\n• `!bot_status` - Check bot status\n• `!test_firebase` - Test Firebase connection",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
async def test_firebase(ctx):
    """Test Firebase connection"""
    if not await guardian.is_firebase_ready():
        await ctx.send("❌ Firebase not connected")
        return
        
    try:
        # Try to write a test document
        test_ref = db.collection('test').document('connection_test')
        test_ref.set({
            'timestamp': firestore.SERVER_TIMESTAMP, 
            'guild_id': str(ctx.guild.id),
            'test': 'success'
        })
        
        # Try to read it back
        doc = test_ref.get()
        if doc.exists:
            await ctx.send("✅ Firebase connection working!")
        else:
            await ctx.send("❌ Firebase write succeeded but read failed")
            
    except Exception as e:
        await ctx.send(f"❌ Firebase error: {e}")

@bot.command()
async def bot_status(ctx):
    """Check bot and Firebase status"""
    firebase_status = "✅ Connected" if await guardian.is_firebase_ready() else "❌ Disconnected"
    immune_roles = await guardian.get_immune_roles(ctx.guild)
    bot_highest_role = await guardian.get_bot_highest_role(ctx.guild)
    
    embed = discord.Embed(title="🤖 Bot Status", color=0x339af0)
    embed.add_field(name="Firebase", value=firebase_status, inline=True)
    embed.add_field(name="Immune Roles", value=f"{len(immune_roles)}", inline=True)
    embed.add_field(name="Bot Highest Role", value=bot_highest_role.name if bot_highest_role else "None", inline=True)
    embed.add_field(name="Guild", value=ctx.guild.name, inline=True)
    embed.add_field(name="Members", value=ctx.guild.member_count, inline=True)
    
    await ctx.send(embed=embed)

# Flask server for Render health checks
app = Flask(__name__)

@app.route('/')
def home():
    return "🛡️ Nickname Guardian Bot - Online"

def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False)

if __name__ == "__main__":
    # Start Flask server in a separate thread for Render health checks
    from threading import Thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start Discord bot
    token = os.environ.get('DISCORD_TOKEN')
    if token:
        logger.info("🚀 Starting bot on Render...")
        bot.run(token)
    else:
        logger.error("❌ DISCORD_TOKEN not found")
