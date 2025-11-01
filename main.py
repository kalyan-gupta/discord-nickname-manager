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

# FIXED Firebase Initialization
firebase_project_id = None

def initialize_firebase():
    global firebase_project_id
    try:
        # Get Firebase config from environment variable
        firebase_config_json = os.environ.get('FIREBASE_CONFIG')
        
        if not firebase_config_json:
            logger.error("❌ FIREBASE_CONFIG environment variable not set")
            logger.error("Please set FIREBASE_CONFIG with your service account JSON")
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

# Firestore client (will be None if Firebase failed)
try:
    if firebase_admin._apps and firebase_project_id:
        db = firestore.AsyncClient(project=firebase_project_id)
        logger.info("✅ Firestore client ready")
    else:
        db = None
        logger.warning("⚠️ Firestore client not available")
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

    async def is_user_immune(self, user, guild):
        """Check if user has immune role"""
        if not await self.is_firebase_ready():
            logger.error("❌ Firebase not available for immunity check")
            return False
            
        try:
            # Get immune roles from Firestore
            immune_roles_ref = db.collection('servers').document(str(guild.id)).collection('immune_roles')
            docs = await immune_roles_ref.get()
            
            immune_role_ids = [doc.id for doc in docs]
            user_role_ids = [str(role.id) for role in user.roles]
            
            # Check if user has any immune role
            has_immunity = any(role_id in user_role_ids for role_id in immune_role_ids)
            logger.debug(f"Immunity check for {user.name}: {has_immunity}")
            return has_immunity
            
        except Exception as e:
            logger.error(f"Error checking immunity: {e}")
            return False

    async def add_immune_role(self, role):
        """Add role to immune list in Firestore"""
        if not await self.is_firebase_ready():
            logger.error("❌ Firebase not available")
            return False
            
        try:
            role_ref = db.collection('servers').document(str(role.guild.id)).collection('immune_roles').document(str(role.id))
            await role_ref.set({
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

    async def get_immune_roles(self, guild):
        """Get all immune roles from Firestore"""
        if not await self.is_firebase_ready():
            logger.error("❌ Firebase not available")
            return []
            
        try:
            roles_ref = db.collection('servers').document(str(guild.id)).collection('immune_roles')
            docs = await roles_ref.get()
            
            immune_roles = []
            for doc in docs:
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
            logger.error(f"Error getting immune roles: {e}")
            return []

# Initialize bot
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
guardian = NicknameGuardian(bot)

@bot.event
async def on_ready():
    logger.info(f'✅ {bot.user} is online!')
    logger.info(f'📊 Connected to {len(bot.guilds)} guild(s)')
    
    # Test Firebase connection
    if await guardian.is_firebase_ready():
        logger.info("✅ Firebase connection verified")
    else:
        logger.warning("⚠️ Firebase not connected - immune role features disabled")

@bot.command()
async def immune_role(ctx, role: discord.Role):
    """Add role to immune list"""
    if not await guardian.is_firebase_ready():
        await ctx.send("❌ Database not available. Please check Firebase configuration.")
        return
        
    success = await guardian.add_immune_role(role)
    if success:
        await ctx.send(f"✅ {role.mention} added to immune list!")
    else:
        await ctx.send("❌ Failed to add role to immune list")

@bot.command()
async def immune_roles(ctx):
    """List immune roles"""
    immune_roles = await guardian.get_immune_roles(ctx.guild)
    
    if not immune_roles:
        await ctx.send("No immune roles configured")
        return
        
    embed = discord.Embed(title="🛡️ Immune Roles", color=0x00ff00)
    for immune_role in immune_roles:
        role = immune_role['role']
        embed.add_field(
            name=role.name,
            value=f"{immune_role['member_count']} members",
            inline=True
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
        await test_ref.set({
            'timestamp': firestore.SERVER_TIMESTAMP, 
            'guild_id': ctx.guild.id,
            'test': 'success'
        })
        
        # Try to read it back
        doc = await test_ref.get()
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
    
    embed = discord.Embed(title="🤖 Bot Status", color=0x339af0)
    embed.add_field(name="Firebase", value=firebase_status, inline=True)
    embed.add_field(name="Immune Roles", value=f"{len(immune_roles)}", inline=True)
    embed.add_field(name="Guild", value=ctx.guild.name, inline=True)
    
    await ctx.send(embed=embed)

# Keep-alive server
app = Flask('')

@app.route('/')
def home():
    return "🛡️ Nickname Guardian Bot"

def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080), daemon=True).start()

# Start bot
if __name__ == "__main__":
    keep_alive()
    token = os.environ.get('DISCORD_TOKEN')
    if token:
        logger.info("🚀 Starting bot...")
        bot.run(token)
    else:
        logger.error("❌ DISCORD_TOKEN not found")
