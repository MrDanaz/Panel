from flask import Flask, render_template, redirect, url_for, session, request
import os
import json

# Load configuration
def load_config():
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("ERROR: config.json not found. Please create it based on config_example.json")
        # You might want to raise an exception or exit here in a real app
        return {} # Return empty dict to avoid crash during early dev
    except json.JSONDecodeError:
        print("ERROR: config.json is not valid JSON.")
        return {}

config = load_config()

# Initialize Flask App
app = Flask(__name__)
app.secret_key = config.get('APP_SECRET_KEY', os.urandom(24)) # Use key from config or fallback

# --- Route Definitions ---

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('overview'))
    return redirect(url_for('login_page')) # Redirect to actual login page

@app.route('/login_page') # Renamed from /login to avoid conflict with discord_login logic
def login_page():
    # This route will render the login.html template
    # The actual Discord login will be initiated by a button/link on this page
    # that points to a new route like /auth/discord
    return render_template('login.html')

from authlib.integrations.flask_client import OAuth
from functools import wraps
import requests # For direct API calls after OAuth

# ... (keep existing imports and config loading) ...

# Initialize Flask App
# app = Flask(__name__) # Already initialized
# app.secret_key = config.get('APP_SECRET_KEY', os.urandom(24)) # Already set

import discord as discordpy # Renamed to avoid conflict with the oauth 'discord' object
from discord.ext import commands
import asyncio
import threading

# Initialize OAuth
oauth = OAuth(app)


# --- Discord.py Bot Setup ---
intents = discordpy.Intents.default()
intents.members = True # Required for accurate member counts and fetching all members
intents.presences = True # Required for online member counts
intents.guilds = True # Required for guild information

bot = commands.Bot(command_prefix="!", intents=intents)
# We are not defining commands here, just using the bot client for API access

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f"Operating in guilds: {[guild.name for guild in bot.guilds]}")
    target_guild_id = config.get("SERVER_ID")
    if target_guild_id:
        guild = bot.get_guild(int(target_guild_id))
        if guild:
            print(f"Target guild '{guild.name}' found.")
        else:
            print(f"Error: Target guild with ID '{target_guild_id}' not found by bot. Ensure bot is in this server.")
    else:
        print("Warning: SERVER_ID not configured in config.json for bot context.")

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        bot_token = config.get('DISCORD_BOT_TOKEN')
        if not bot_token:
            print("CRITICAL: DISCORD_BOT_TOKEN is not set in config.json. Bot cannot start.")
            return
        loop.run_until_complete(bot.start(bot_token))
    except Exception as e:
        print(f"Error running bot: {e}")
    finally:
        loop.close()

# Start the bot in a separate thread if BOT_TOKEN is available
if config.get('DISCORD_BOT_TOKEN'):
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    # bot_thread.start() # Will start later, after initial app setup, or on first request to ensure config is loaded.
else:
    print("Skipping Discord bot startup as DISCORD_BOT_TOKEN is not configured.")
    bot_thread = None


# Configure Discord OAuth (Authlib client)
# 'discord' here is the Authlib OAuth client, not the discord.py bot instance
discord_oauth_client = oauth.register( # Renamed to avoid confusion
    name='discord_oauth', # Name used by Authlib, distinct from discord.py bot
    client_id=config.get('DISCORD_CLIENT_ID'),
    client_secret=config.get('DISCORD_CLIENT_SECRET'),
    access_token_url='https://discord.com/api/oauth2/token',
    access_token_params=None,
    authorize_url='https://discord.com/api/oauth2/authorize',
    authorize_params=None,
    api_base_url='https://discord.com/api/v10/', # Using API v10
    userinfo_endpoint='users/@me', # Endpoint to fetch user info
    client_kwargs={'scope': 'identify email guilds guilds.join guilds.members.read'}, # Add necessary scopes
    # guilds.members.read is privileged, ensure bot has it and server members intent is enabled
    # guilds.join might be needed if you plan to add the bot to servers via user auth
)


# --- Helper Functions ---
def get_user_guilds(token):
    """Fetches user's guilds."""
    headers = {'Authorization': f'Bearer {token["access_token"]}'}
    # Use the api_base_url from the oauth client instance
    resp = requests.get(f"{discord_oauth_client.api_base_url}users/@me/guilds", headers=headers)
    resp.raise_for_status() # Ensure we catch HTTP errors
    return resp.json()

def get_user_roles_in_guild(user_id, guild_id):
    """
    Fetches a user's roles in a specific guild using the BOT token.
    This is necessary because the user's OAuth token might not have permissions
    to read all roles, but the bot should.
    """
    bot_token = config.get('DISCORD_BOT_TOKEN')
    if not bot_token:
        print("Error: DISCORD_BOT_TOKEN not configured.")
        return []

    headers = {'Authorization': f'Bot {bot_token}'}
    # Use the correct api_base_url for discord.py bot interactions if different,
    # but usually it's the same. discord_oauth_client.api_base_url is fine.
    member_url = f"{discord_oauth_client.api_base_url}guilds/{guild_id}/members/{user_id}"

    try:
        resp = requests.get(member_url, headers=headers)
        resp.raise_for_status() # Catch HTTP errors
        member_data = resp.json()
        return member_data.get('roles', [])
    except requests.exceptions.HTTPError as err:
        print(f"HTTP Error fetching member roles for user {user_id} in guild {guild_id}: {err}")
        if err.response:
            print(f"Response content: {err.response.content}")
            if err.response.status_code == 404:
                print(f"User {user_id} not found in guild {guild_id}, or bot lacks permissions/intent.")
            elif err.response.status_code == 401:
                print("Bot token is invalid or missing necessary permissions (401 Unauthorized).")
            elif err.response.status_code == 403:
                print("Bot token does not have permissions for this action (403 Forbidden). Ensure Server Members Intent.")
        return [] # Return empty list on error
    except Exception as e: # Catch other errors like network issues
        print(f"An unexpected error occurred while fetching roles for user {user_id} in guild {guild_id}: {e}")
        return []

# --- Authentication Routes ---

@app.route('/auth/discord') # This is where the "Login with Discord" button points
def discord_login():
    redirect_uri = url_for('discord_authorize', _external=True)
    # Ensure DISCORD_REDIRECT_URI in config.json matches this exact _external=True URI
    # e.g., http://localhost:5000/auth/discord/authorize if running locally
    # or your production URI
    print(f"Generated redirect_uri for Discord: {redirect_uri}")
    # It should match one of the URIs in your Discord App's OAuth2 settings
    if not config.get('DISCORD_REDIRECT_URI') or redirect_uri != config.get('DISCORD_REDIRECT_URI'):
        print(f"WARNING: Generated redirect_uri '{redirect_uri}' does not match DISCORD_REDIRECT_URI '{config.get('DISCORD_REDIRECT_URI')}' from config.json.")
        if config.get('DISCORD_REDIRECT_URI'):
            redirect_uri = config.get('DISCORD_REDIRECT_URI')
        else:
            # Log this critical error, but don't expose detailed error to user here.
            # The login page will show a generic error from the redirect.
            print("CRITICAL: Discord redirect URI not configured correctly and no fallback in config.")
            return redirect(url_for('login_page', error="OAuth setup error"))


    return discord_oauth_client.authorize_redirect(redirect_uri)

@app.route('/auth/discord/authorize') # This is the callback URL registered with Discord
def discord_authorize():
    try:
        token = discord_oauth_client.authorize_access_token()
    except Exception as e:
        print(f"Error during authorize_access_token: {e}")
        return redirect(url_for('login_page', error="OAuth token fetch failed"))

    if not token or 'access_token' not in token:
        return redirect(url_for('login_page', error="Invalid token received"))

    session['discord_oauth_token'] = token

    user_info_resp = discord_oauth_client.get('users/@me')
    if not user_info_resp.ok:
        return redirect(url_for('login_page', error="Failed to fetch user info"))

    user_data = user_info_resp.json()
    session['user_id'] = user_data.get('id')
    session['user_name'] = f"{user_data.get('username')}#{user_data.get('discriminator')}"
    session['user_avatar'] = user_data.get('avatar') # For display later, if desired

    # Check if user is in the configured server and get their roles
    target_server_id = config.get('SERVER_ID')
    if not target_server_id:
        print("Error: SERVER_ID not configured.")
        return redirect(url_for('login_page', error="Server ID not configured."))

    # Method 1: Check via users/@me/guilds (if user has many guilds, this list can be large)
    # user_guilds = get_user_guilds(token)
    # server_found = any(g['id'] == target_server_id for g in user_guilds)
    # if not server_found:
    #     session.clear()
    #     return redirect(url_for('login_page', error="User not in the required Discord server."))

    # Method 2: Directly try to fetch member data from the specific guild using BOT token
    # This is generally more reliable for getting roles.
    user_roles_in_server = get_user_roles_in_guild(session['user_id'], target_server_id)

    if not user_roles_in_server: # This could mean user not in server OR bot cannot see them
        # To differentiate, we might need to use discord.py bot instance to check guild.get_member()
        # For now, assume if no roles, they are not a relevant member or an issue with bot permissions.
        print(f"User {session['user_id']} not found in server {target_server_id} or no roles retrieved.")
        # A more robust check would be to use the bot to see if the member exists at all.
        # If the bot confirms they exist but has no roles, then they are a member with no specific team roles.
        # If the bot can't find them, they are not on the server.
        # For simplicity here, if get_user_roles_in_guild returns empty, we deny access to team features.
        # This needs the bot to have Server Members Intent enabled.

        # Check if user is on server AT ALL (requires bot context or separate check)
        # For now, we'll assume if no roles are fetched, they might not be on the server
        # or have no relevant roles. A stricter check is needed for "user not in server".
        # This basic check is okay if we only care about users with specific roles.
        pass # Allow login, but they won't have permissions if they don't have team roles.


    session['user_roles'] = user_roles_in_server # Store list of role IDs

    # Check if the user has any of the defined "Teamler Rollen"
    teamler_rollen_ids = set(config.get("TEAMLER_ROLLEN_IDS", []))
    user_role_ids_set = set(user_roles_in_server)

    is_team_member = bool(teamler_rollen_ids.intersection(user_role_ids_set))

    if not is_team_member:
        # Potentially log them out or redirect to a "not authorized" page
        # For now, let them log in, but permission checks will restrict access
        print(f"User {session['user_name']} ({session['user_id']}) logged in but is not a designated team member based on roles.")
        # session.clear()
        # return redirect(url_for('login_page', error="You are not authorized to use this panel."))


    return redirect(url_for('overview'))


# --- Permission Decorators ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def requires_permission(permission_name):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session: # Should be caught by @login_required first if used together
                return redirect(url_for('login_page', next=request.url))

            user_roles = session.get('user_roles', [])
            if not user_roles: # No roles, no permissions
                # flash("You do not have any roles assigned.", "danger")
                return redirect(url_for('overview', error="No roles assigned")) # Or a dedicated error page

            # Check permissions from config
            config_permissions = config.get('PERMISSIONS', {})
            has_perm = False
            for role_id in user_roles:
                role_perms = config_permissions.get(str(role_id), {}) # Ensure role_id is string for JSON keys
                if role_perms.get(permission_name, False) is True:
                    has_perm = True
                    break

            if not has_perm:
                # flash(f"You do not have permission to '{permission_name}'.", "danger")
                # You might want to redirect to a specific "access denied" page or just the overview
                return redirect(url_for('overview', error=f"Access denied for: {permission_name}"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.route('/logout')
@login_required # Technically not needed as it clears session, but good practice
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/overview')
@login_required
def overview():
    stats = {
        'total_members': 'N/A',
        'online_members': 'N/A',
        'banned_members': 'N/A', # This is hard to get accurately without iterating all bans or specific bot commands
        'team_members': 'N/A'
    }
    server_id = config.get("SERVER_ID")
    if not bot.is_ready() or not server_id:
        print("Bot not ready or SERVER_ID not configured. Stats will be N/A.")
        if not bot.is_ready() and bot_thread and not bot_thread.is_alive() and config.get('DISCORD_BOT_TOKEN'):
            print("Bot thread is not alive. Attempting to start it.")
            # Ensure it's only started once. A more robust check might be needed.
            # For now, let's assume it's started at app launch or handle this more gracefully.
            # If it failed to start due to bad token, this won't help.
            # A check could be: if not getattr(app, 'bot_started', False): app.bot_started = True; bot_thread.start()
            pass
        return render_template('overview.html', stats=stats, error="Bot not connected or server not configured.")

    guild = bot.get_guild(int(server_id))
    if guild:
        stats['total_members'] = guild.member_count

        # Online members (excluding bots, can be refined)
        stats['online_members'] = sum(1 for m in guild.members if m.status != discordpy.Status.offline and not m.bot)

        # Team members
        team_haupt_rolle_id = config.get("TEAMLER_HAUPT_ROLLE_ID")
        if team_haupt_rolle_id:
            try:
                team_role = guild.get_role(int(team_haupt_rolle_id))
                if team_role:
                    stats['team_members'] = len(team_role.members)
                else:
                    stats['team_members'] = 'Role N/A'
            except ValueError:
                stats['team_members'] = 'Invalid Role ID'
        else:
            stats['team_members'] = 'Haupt Rolle N/A'

        # Banned members count - this requires iterating through bans if available via API
        # Or a bot command that specifically fetches this.
        # For now, we'll leave it as N/A as it's a privileged operation.
        # bans = await guild.bans() # This is an async operation, needs to be handled in an async context
        # stats['banned_members'] = len(bans)
        # To do this properly, we'd need an async helper or run this part in bot's loop.
        # For simplicity, let's assume a bot command could update this, or it's fetched differently.
        stats['banned_members'] = 'N/A (requires specific bot command or async call)'

    else:
        print(f"Bot is not in the configured server ID: {server_id}")
        return render_template('overview.html', stats=stats, error=f"Bot not in server {server_id}")

    return render_template('overview.html', stats=stats)

@app.route('/teamlist')
@login_required
@requires_permission('view_team_list')
def team_list():
    team_members_data = []
    server_id = config.get("SERVER_ID")
    teamler_rollen_ids_str = config.get("TEAMLER_ROLLEN_IDS", []) # These are strings from JSON

    # Convert role IDs from config to int for comparison with discord.py role objects
    # Also, create a mapping from role ID to a preference or hierarchy if needed for "highest role"
    # For simplicity, we can use the order in config or sort by actual role position later.
    # For now, let's get all roles defined in TEAMLER_ROLLEN_IDS.

    all_team_role_ids_int = set()
    try:
        for r_id_str in teamler_rollen_ids_str:
            all_team_role_ids_int.add(int(r_id_str))
    except ValueError:
        print("Error: Invalid (non-integer) role ID found in TEAMLER_ROLLEN_IDS config.")
        return render_template('team_list.html', team_members=[], error="Invalid team role ID in config.", available_roles_for_promotion_or_demotion=[])

    if not bot.is_ready() or not server_id:
        return render_template('team_list.html', team_members=[], error="Bot not connected or server not configured.", available_roles_for_promotion_or_demotion=[])

    guild = bot.get_guild(int(server_id))
    if not guild:
        return render_template('team_list.html', team_members=[], error=f"Bot not in server {server_id}", available_roles_for_promotion_or_demotion=[])

    # Fetch all team roles from the guild to get their names and positions
    guild_team_roles = {} # Store role_id: {name, position}
    for r_id_int in all_team_role_ids_int:
        role = guild.get_role(r_id_int)
        if role:
            guild_team_roles[r_id_int] = {'name': role.name, 'position': role.position, 'id_str': str(r_id_int)}
        else:
            print(f"Warning: Team role ID {r_id_int} from config not found in guild {guild.name}")
            # Add a placeholder or skip if the role doesn't exist on the server
            guild_team_roles[r_id_int] = {'name': f"Unknown Role ({r_id_int})", 'position': -1, 'id_str': str(r_id_int)}


    for member in guild.members:
        if member.bot: # Skip bots
            continue

        member_team_roles = []
        for role in member.roles:
            if role.id in all_team_role_ids_int:
                member_team_roles.append(guild_team_roles[role.id])

        if member_team_roles:
            # Sort roles by position (highest first) to find the top role
            member_team_roles.sort(key=lambda r: r['position'], reverse=True)
            highest_role_name = member_team_roles[0]['name'] if member_team_roles else "N/A"

            team_members_data.append({
                'id': str(member.id),
                'name': f"{member.name}#{member.discriminator}",
                'display_name': member.display_name,
                'highest_role_name': highest_role_name,
                'avatar_url': str(member.avatar.url) if member.avatar else None
            })

    # Sort team members, e.g., by display name or highest role position
    # For now, let's use the order fetched (usually by join date or ID)

    # For "promote/demote" modal, provide list of configurable team roles
    # Sort them by position for the dropdown
    available_roles_for_modal = sorted(list(guild_team_roles.values()), key=lambda r: r['position'], reverse=True)
    # Format for template: [(role_id_str, role_name), ...]
    formatted_available_roles = [(r['id_str'], r['name']) for r in available_roles_for_modal if r['position'] != -1]


    return render_template('team_list.html',
                           team_members=team_members_data,
                           available_roles_for_promotion_or_demotion=formatted_available_roles,
                           error=request.args.get('error'),
                           success=request.args.get('success'))


@app.route('/members')
@login_required
@requires_permission('view_members')
def members_list():
    all_members_data = []
    server_id = config.get("SERVER_ID")

    if not bot.is_ready() or not server_id:
        return render_template('members_list.html', members=[], error="Bot not connected or server not configured.")

    guild = bot.get_guild(int(server_id))
    if not guild:
        return render_template('members_list.html', members=[], error=f"Bot not in server {server_id}")

    # Fetching all members can be intensive on very large servers.
    # guild.members is usually populated if Members intent is on and bot has fetched them.
    for member in guild.members:
        if member.bot: # Optionally skip bots, or show them if desired
            # continue
            pass

        all_members_data.append({
            'id': str(member.id),
            'name': member.name,
            'discriminator': member.discriminator,
            'full_name': f"{member.name}#{member.discriminator}",
            'display_name': member.display_name,
            'status': str(member.status), # e.g., "online", "offline", "idle", "dnd"
            'joined_at': member.joined_at.strftime('%d.%m.%Y %H:%M:%S') if member.joined_at else 'N/A',
            'avatar_url': str(member.avatar.url) if member.avatar else None,
            'is_bot': member.bot
        })

    # Sort members, e.g., by name or join date
    all_members_data.sort(key=lambda m: m['joined_at'], reverse=True) # Example: newest first

    return render_template('members_list.html',
                           members=all_members_data,
                           error=request.args.get('error'),
                           success=request.args.get('success'))


@app.route('/applications')
@login_required
@requires_permission('view_applications') # Example permission
def applications():
    return render_template('applications.html')

@app.route('/complaints')
@login_required
@requires_permission('view_complaints') # Example permission
def complaints():
    return render_template('complaints.html')

# --- Placeholder routes for form submissions (functionality to be implemented later) ---
# Apply @login_required and @requires_permission decorators as needed to these action routes

@app.route('/send_announcement', methods=['POST'])
@login_required
@requires_permission('send_announcements') # Example permission
def send_announcement():
    message_content = request.form.get('message')
    ank_channel_id = config.get('ANKUENDIGUNG_KANAL_ID')

    if not bot.is_ready():
        return redirect(url_for('overview', error="Bot not ready to send message."))
    if not ank_channel_id:
        return redirect(url_for('overview', error="Announcement channel ID not configured."))
    if not message_content:
        return redirect(url_for('overview', error="Announcement message cannot be empty."))

    try:
        channel = bot.get_channel(int(ank_channel_id))
        if channel and isinstance(channel, discordpy.TextChannel):
            # Run the async send in the bot's event loop
            asyncio.run_coroutine_threadsafe(channel.send(f"**Ankündigung von {session.get('user_name', 'Panel')}:**\n{message_content}"), bot.loop)
            # flash("Announcement sent successfully!", "success") # If you implement flash messages
        else:
            return redirect(url_for('overview', error="Announcement channel not found or not a text channel."))
    except ValueError:
        return redirect(url_for('overview', error="Invalid Announcement channel ID format."))
    except Exception as e:
        print(f"Error sending announcement: {e}")
        return redirect(url_for('overview', error=f"Failed to send announcement: {e}"))

    return redirect(url_for('overview', success="Announcement sent."))

@app.route('/submit_absence', methods=['POST'])
@login_required # Any logged-in team member can submit absence
def submit_absence():
    reason = request.form.get('reason')
    duration = request.form.get('duration')
    abm_channel_id = config.get('ABMELDUNG_KANAL_ID')

    if not bot.is_ready():
        return redirect(url_for('overview', error="Bot not ready to send absence message."))
    if not abm_channel_id:
        return redirect(url_for('overview', error="Absence channel ID not configured."))
    if not reason or not duration:
        return redirect(url_for('overview', error="Absence reason and duration are required."))

    try:
        channel = bot.get_channel(int(abm_channel_id))
        if channel and isinstance(channel, discordpy.TextChannel):
            user_who_submitted = session.get('user_name', 'Ein Teamler')
            message = f"**Abmeldung von {user_who_submitted}:**\n**Grund:** {reason}\n**Dauer:** {duration}"
            asyncio.run_coroutine_threadsafe(channel.send(message), bot.loop)
        else:
            return redirect(url_for('overview', error="Absence channel not found or not a text channel."))
    except ValueError:
        return redirect(url_for('overview', error="Invalid Absence channel ID format."))
    except Exception as e:
        print(f"Error submitting absence: {e}")
        return redirect(url_for('overview', error=f"Failed to submit absence: {e}"))

    return redirect(url_for('overview', success="Absence submitted."))

@app.route('/promote_member', methods=['POST'])
@login_required
@requires_permission('manage_team_list')
def promote_member():
    member_id_str = request.form.get('member_id')
    new_role_id_str = request.form.get('role_id') # This is the ID of the role to add
    action_user_name = session.get('user_name', 'Panel Admin')

    if not bot.is_ready() or not member_id_str or not new_role_id_str:
        return redirect(url_for('team_list', error="Bot not ready or missing member/role ID."))

    guild = bot.get_guild(int(config.get("SERVER_ID")))
    if not guild:
        return redirect(url_for('team_list', error="Guild not found by bot."))

    try:
        member_id = int(member_id_str)
        new_role_id = int(new_role_id_str)
        member = guild.get_member(member_id)
        new_role = guild.get_role(new_role_id)

        if not member or not new_role:
            return redirect(url_for('team_list', error="Member or new role not found in guild."))

        # Logic: Add the new role. If it's a promotion, you might also want to remove lower team roles.
        # For simplicity, this example just adds the new role.
        # A more complex setup would compare positions and manage multiple team roles.
        # Ensure the new role is part of the TEAMLER_ROLLEN_IDS from config.
        if str(new_role.id) not in config.get("TEAMLER_ROLLEN_IDS", []):
            return redirect(url_for('team_list', error="Selected role is not a valid team role for promotion."))

        asyncio.run_coroutine_threadsafe(member.add_roles(new_role, reason=f"Promoted by {action_user_name} via Admin Panel"), bot.loop)

        # Send update to Team Updates Kanal
        team_updates_kanal_id = config.get("TEAM_UPDATES_KANAL_ID")
        if team_updates_kanal_id:
            channel = bot.get_channel(int(team_updates_kanal_id))
            if channel:
                msg = f":arrow_up: **Promotion:** {member.mention} (`{member.id}`) wurde von {action_user_name} zur Rolle '{new_role.name}' befördert."
                asyncio.run_coroutine_threadsafe(channel.send(msg), bot.loop)

        return redirect(url_for('team_list', success=f"{member.display_name} promoted to {new_role.name}."))
    except ValueError:
        return redirect(url_for('team_list', error="Invalid ID format."))
    except Exception as e:
        print(f"Error during promotion: {e}")
        return redirect(url_for('team_list', error=f"Promotion failed: {e}"))


@app.route('/demote_member', methods=['POST'])
@login_required
@requires_permission('manage_team_list')
def demote_member():
    member_id_str = request.form.get('member_id')
    role_to_remove_id_str = request.form.get('role_id') # This is the role to remove for demotion
    action_user_name = session.get('user_name', 'Panel Admin')

    if not bot.is_ready() or not member_id_str or not role_to_remove_id_str:
        return redirect(url_for('team_list', error="Bot not ready or missing member/role ID for demotion."))

    guild = bot.get_guild(int(config.get("SERVER_ID")))
    if not guild:
        return redirect(url_for('team_list', error="Guild not found by bot."))

    try:
        member_id = int(member_id_str)
        role_to_remove_id = int(role_to_remove_id_str)
        member = guild.get_member(member_id)
        role_to_remove = guild.get_role(role_to_remove_id)

        if not member or not role_to_remove:
            return redirect(url_for('team_list', error="Member or role to remove not found in guild."))

        # Ensure the role to remove is part of the TEAMLER_ROLLEN_IDS
        if str(role_to_remove.id) not in config.get("TEAMLER_ROLLEN_IDS", []):
            return redirect(url_for('team_list', error="Selected role is not a valid team role for demotion."))

        asyncio.run_coroutine_threadsafe(member.remove_roles(role_to_remove, reason=f"Demoted by {action_user_name} via Admin Panel"), bot.loop)

        team_updates_kanal_id = config.get("TEAM_UPDATES_KANAL_ID")
        if team_updates_kanal_id:
            channel = bot.get_channel(int(team_updates_kanal_id))
            if channel:
                # You might want to specify which role they were demoted FROM, or to what new (lower) role if applicable.
                msg = f":arrow_down: **Degradierung:** {member.mention} (`{member.id}`) wurde von {action_user_name} von der Rolle '{role_to_remove.name}' degradiert/entfernt."
                asyncio.run_coroutine_threadsafe(channel.send(msg), bot.loop)

        return redirect(url_for('team_list', success=f"{member.display_name} demoted (role {role_to_remove.name} removed)."))
    except ValueError:
        return redirect(url_for('team_list', error="Invalid ID format for demotion."))
    except Exception as e:
        print(f"Error during demotion: {e}")
        return redirect(url_for('team_list', error=f"Demotion failed: {e}"))


@app.route('/warn_member', methods=['POST'])
@login_required
@requires_permission('manage_team_list') # Or a more specific "warn_team_member" permission
def warn_member():
    member_id_str = request.form.get('member_id')
    reason = request.form.get('reason')
    action_user_name = session.get('user_name', 'Panel Admin')

    if not reason:
        return redirect(url_for('team_list', error="Reason is required for warning a team member."))
    if not bot.is_ready() or not member_id_str:
        return redirect(url_for('team_list', error="Bot not ready or missing member ID for warning."))

    guild = bot.get_guild(int(config.get("SERVER_ID")))
    if not guild:
        return redirect(url_for('team_list', error="Guild not found by bot."))

    try:
        member_id = int(member_id_str)
        member = guild.get_member(member_id)
        if not member:
            return redirect(url_for('team_list', error="Member to warn not found in guild."))

        # Send DM to member (optional, consider privacy and bot permissions)
        try:
            dm_message = f"Du hast eine Verwarnung vom Team erhalten.\n**Grund:** {reason}\n*Diese Verwarnung wurde von {action_user_name} über das Admin Panel ausgestellt.*"
            asyncio.run_coroutine_threadsafe(member.send(dm_message), bot.loop)
        except discordpy.Forbidden:
            print(f"Could not DM user {member.id}, DMs might be disabled.")
        except Exception as e:
            print(f"Error sending DM for warning: {e}")

        # Send to Team Updates Kanal
        team_updates_kanal_id = config.get("TEAM_UPDATES_KANAL_ID")
        if team_updates_kanal_id:
            channel = bot.get_channel(int(team_updates_kanal_id))
            if channel:
                msg = f":warning: **Verwarnung:** {member.mention} (`{member.id}`) wurde von {action_user_name} verwarnt.\n**Grund:** {reason}"
                asyncio.run_coroutine_threadsafe(channel.send(msg), bot.loop)

        return redirect(url_for('team_list', success=f"{member.display_name} has been warned. Reason: {reason}"))
    except ValueError:
        return redirect(url_for('team_list', error="Invalid member ID format for warning."))
    except Exception as e:
        print(f"Error during warning: {e}")
        return redirect(url_for('team_list', error=f"Warning failed: {e}"))


@app.route('/kick_team_member', methods=['POST'])
@login_required
@requires_permission('manage_team_list') # Or "kick_from_team"
def kick_team_member():
    member_id_str = request.form.get('member_id')
    reason = request.form.get('reason')
    action_user_name = session.get('user_name', 'Panel Admin')

    if not reason:
        return redirect(url_for('team_list', error="Reason is required for kicking a member from the team."))
    if not bot.is_ready() or not member_id_str:
        return redirect(url_for('team_list', error="Bot not ready or missing member ID for team kick."))

    guild = bot.get_guild(int(config.get("SERVER_ID")))
    if not guild:
        return redirect(url_for('team_list', error="Guild not found by bot."))

    try:
        member_id = int(member_id_str)
        member = guild.get_member(member_id)
        if not member:
            return redirect(url_for('team_list', error="Member to kick from team not found in guild."))

        # Remove all team roles defined in TEAMLER_ROLLEN_IDS
        team_roles_to_remove = []
        for role_id_str in config.get("TEAMLER_ROLLEN_IDS", []):
            role = guild.get_role(int(role_id_str))
            if role and role in member.roles:
                team_roles_to_remove.append(role)

        if team_roles_to_remove:
            asyncio.run_coroutine_threadsafe(member.remove_roles(*team_roles_to_remove, reason=f"Aus dem Team entfernt von {action_user_name} via Admin Panel. Grund: {reason}"), bot.loop)

        # Send DM (optional)
        try:
            dm_message = f"Du wurdest aus dem Team entfernt.\n**Grund:** {reason}\n*Diese Aktion wurde von {action_user_name} über das Admin Panel durchgeführt.*"
            asyncio.run_coroutine_threadsafe(member.send(dm_message), bot.loop)
        except Exception as e:
            print(f"Error sending DM for team kick: {e}")

        # Send to Team Updates Kanal
        team_updates_kanal_id = config.get("TEAM_UPDATES_KANAL_ID")
        if team_updates_kanal_id:
            channel = bot.get_channel(int(team_updates_kanal_id))
            if channel:
                msg = f":door: **Aus Team entfernt:** {member.mention} (`{member.id}`) wurde von {action_user_name} aus dem Team entfernt.\n**Grund:** {reason}"
                asyncio.run_coroutine_threadsafe(channel.send(msg), bot.loop)

        return redirect(url_for('team_list', success=f"{member.display_name} has been kicked from the team. Reason: {reason}"))
    except ValueError:
        return redirect(url_for('team_list', error="Invalid member ID format for team kick."))
    except Exception as e:
        print(f"Error during team kick: {e}")
        return redirect(url_for('team_list', error=f"Team kick failed: {e}"))


@app.route('/timeout_member', methods=['POST'])
import datetime as dt # For timeout duration calculation

@app.route('/timeout_member', methods=['POST'])
@login_required
@requires_permission('manage_members')
def timeout_member():
    member_id_str = request.form.get('member_id')
    duration_str = request.form.get('duration') # e.g., "60s", "5m", "1h", "1d", "1w"
    reason = request.form.get('reason') or f"Timed out by {session.get('user_name', 'Panel Admin')} via Admin Panel."
    action_user_name = session.get('user_name', 'Panel Admin')

    if not bot.is_ready():
        return redirect(url_for('members_list', error="Bot is not ready. Cannot perform timeout."))
    if not member_id_str or not duration_str:
        return redirect(url_for('members_list', error="Member ID and duration are required for timeout."))

    server_id_str = config.get("SERVER_ID")
    if not server_id_str:
        return redirect(url_for('members_list', error="Server ID not configured."))

    guild = bot.get_guild(int(server_id_str))
    if not guild:
        return redirect(url_for('members_list', error=f"Guild {server_id_str} not found by bot."))

    try:
        member_id = int(member_id_str)
        member = guild.get_member(member_id) # Fetches from cache, might be None if member not cached or left.
        if not member:
             # Attempt to fetch the member if not in cache - this is an async operation
             # For simplicity in a sync route, we'll rely on cache or consider it "not found"
             # A more robust solution might involve an async helper.
            return redirect(url_for('members_list', error=f"Member with ID {member_id} not found in server cache."))

        # Robust Duration Parsing
        parsed_value = None
        unit = None
        if duration_str[-1].isalpha():
            try:
                parsed_value = int(duration_str[:-1])
                unit = duration_str[-1].lower()
            except ValueError:
                return redirect(url_for('members_list', error="Invalid duration format. Value must be integer."))
        else: # Assume seconds if no unit
             try:
                parsed_value = int(duration_str)
                unit = 's'
             except ValueError:
                return redirect(url_for('members_list', error="Invalid duration format. Value must be integer."))

        delta = None
        if unit == 's':
            delta = dt.timedelta(seconds=parsed_value)
        elif unit == 'm':
            delta = dt.timedelta(minutes=parsed_value)
        elif unit == 'h':
            delta = dt.timedelta(hours=parsed_value)
        elif unit == 'd':
            delta = dt.timedelta(days=parsed_value)
        elif unit == 'w':
            delta = dt.timedelta(weeks=parsed_value)
        else:
            return redirect(url_for('members_list', error="Invalid duration unit. Use s, m, h, d, w."))

        # Discord API timeout limits (min 5 seconds, max 28 days)
        # Note: discord.py might handle min duration, but explicit check is good.
        min_timeout_seconds = 5
        if delta.total_seconds() < min_timeout_seconds:
            return redirect(url_for('members_list', error=f"Timeout duration must be at least {min_timeout_seconds} seconds."))
        if delta > dt.timedelta(days=28):
            return redirect(url_for('members_list', error="Timeout duration cannot exceed 28 days."))

        # Perform the timeout
        actual_reason = reason if reason else f"Timed out by {action_user_name} via Admin Panel."
        asyncio.run_coroutine_threadsafe(member.timeout(delta, reason=actual_reason), bot.loop)

        # Log to configured channel
        log_channel_id_str = config.get("TEAM_UPDATES_KANAL_ID") # Or a specific moderation log channel
        if log_channel_id_str: # Check if the ID string exists
            try:
                log_channel_id = int(log_channel_id_str)
                channel = bot.get_channel(log_channel_id)
                if channel and isinstance(channel, discordpy.TextChannel):
                    msg = f":timer: **Timeout:** {member.mention} (`{member.id}`) wurde von {action_user_name} für {duration_str} getimedout.\n**Grund:** {actual_reason}"
                    asyncio.run_coroutine_threadsafe(channel.send(msg), bot.loop)
                elif channel:
                    print(f"Warning: Log channel {log_channel_id_str} is not a text channel.")
                else:
                    print(f"Warning: Log channel {log_channel_id_str} not found by bot.")
            except ValueError:
                print(f"Warning: Invalid format for TEAM_UPDATES_KANAL_ID: {log_channel_id_str}")
            except Exception as e:
                print(f"Error logging timeout to channel: {e}")


        return redirect(url_for('members_list', success=f"{member.display_name} timed out for {duration_str}."))
    except ValueError: # Catches errors from int(member_id_str) or int(duration_str[:-1]) if not caught by specific parsing
        return redirect(url_for('members_list', error="Invalid member ID or duration value format."))
    except discordpy.Forbidden:
        return redirect(url_for('members_list', error="Bot lacks permission to timeout this member (check roles and hierarchy)."))
    except discordpy.HTTPException as http_err: # More specific error for Discord API issues
        print(f"Discord API Error during timeout: {http_err}")
        return redirect(url_for('members_list', error=f"Discord API error during timeout: {http_err.text} (Code: {http_err.status})"))
    except Exception as e: # Generic catch-all for other unexpected errors
        print(f"Unexpected error during timeout: {e}")
        return redirect(url_for('members_list', error=f"An unexpected error occurred during timeout: {type(e).__name__}"))


@app.route('/kick_member', methods=['POST'])
@login_required
@requires_permission('manage_members')
def kick_member():
    member_id_str = request.form.get('member_id')
    reason = request.form.get('reason') or f"Kicked by {session.get('user_name', 'Panel Admin')} via Admin Panel."
    action_user_name = session.get('user_name', 'Panel Admin')
    actual_reason = reason if reason else f"Kicked by {action_user_name} via Admin Panel."

    if not bot.is_ready():
        return redirect(url_for('members_list', error="Bot is not ready. Cannot perform kick."))
    if not member_id_str:
        return redirect(url_for('members_list', error="Member ID is required for kick."))

    server_id_str = config.get("SERVER_ID")
    if not server_id_str:
        return redirect(url_for('members_list', error="Server ID not configured."))

    guild = bot.get_guild(int(server_id_str))
    if not guild:
        return redirect(url_for('members_list', error=f"Guild {server_id_str} not found by bot."))

    try:
        member_id = int(member_id_str)
        member = guild.get_member(member_id)
        if not member:
            return redirect(url_for('members_list', error=f"Member with ID {member_id} not found in server cache."))

        # Attempt to DM user before kicking (best effort)
        try:
            dm_message = f"Du wurdest vom Server '{guild.name}' gekickt.\n**Grund:** {actual_reason}"
            asyncio.run_coroutine_threadsafe(member.send(dm_message), bot.loop)
        except discordpy.Forbidden:
            print(f"Could not DM user {member.id} about kick (DMs disabled or bot blocked).")
        except Exception as e:
            print(f"Error sending DM for kick to {member.id}: {e}")

        # Perform the kick
        awaitable_kick = member.kick(reason=actual_reason)
        future = asyncio.run_coroutine_threadsafe(awaitable_kick, bot.loop)
        future.result() # Wait for kick to complete to ensure member is gone before logging success

        # Log to configured channel
        log_channel_id_str = config.get("TEAM_UPDATES_KANAL_ID")
        if log_channel_id_str:
            try:
                log_channel_id = int(log_channel_id_str)
                channel = bot.get_channel(log_channel_id)
                if channel and isinstance(channel, discordpy.TextChannel):
                    msg = f":boot: **Kick:** {member.name}#{member.discriminator} (`{member.id}`) wurde von {action_user_name} vom Server gekickt.\n**Grund:** {actual_reason}"
                    asyncio.run_coroutine_threadsafe(channel.send(msg), bot.loop)
                else: # Log channel not found or not text
                    print(f"Warning: Kick log channel {log_channel_id_str} not found or not a text channel.")
            except ValueError: # Invalid channel ID format
                 print(f"Warning: Invalid format for TEAM_UPDATES_KANAL_ID: {log_channel_id_str}")
            except Exception as e: # Other errors during logging
                print(f"Error logging kick to channel: {e}")

        return redirect(url_for('members_list', success=f"{member.name}#{member.discriminator} has been kicked."))
    except ValueError:
        return redirect(url_for('members_list', error="Invalid member ID format."))
    except discordpy.Forbidden:
        return redirect(url_for('members_list', error="Bot lacks permission to kick this member (check roles and hierarchy)."))
    except discordpy.HTTPException as http_err:
        print(f"Discord API Error during kick: {http_err}")
        return redirect(url_for('members_list', error=f"Discord API error during kick: {http_err.text} (Code: {http_err.status})"))
    except Exception as e:
        print(f"Unexpected error during kick: {e}")
        return redirect(url_for('members_list', error=f"An unexpected error occurred during kick: {type(e).__name__}"))


@app.route('/ban_member', methods=['POST'])
@login_required
@requires_permission('manage_members')
def ban_member():
    member_id_str = request.form.get('member_id')
    reason = request.form.get('reason') or f"Banned by {session.get('user_name', 'Panel Admin')} via Admin Panel."
    action_user_name = session.get('user_name', 'Panel Admin')
    # delete_message_seconds: 0 to 604800 (7 days). Default to 0 (don't delete messages).
    delete_message_seconds = 0 # This could be made configurable in the UI later.
    actual_reason = reason if reason else f"Banned by {action_user_name} via Admin Panel."

    if not bot.is_ready():
        return redirect(url_for('members_list', error="Bot is not ready. Cannot perform ban."))
    if not member_id_str:
        return redirect(url_for('members_list', error="Member ID is required for ban."))

    server_id_str = config.get("SERVER_ID")
    if not server_id_str:
        return redirect(url_for('members_list', error="Server ID not configured."))

    guild = bot.get_guild(int(server_id_str))
    if not guild:
        return redirect(url_for('members_list', error=f"Guild {server_id_str} not found by bot."))

    try:
        member_id = int(member_id_str)
        user_object_to_ban = discordpy.Object(id=member_id) # Allows banning users not currently in server by ID

        # Attempt to get user's name for logging. This is best-effort.
        # If member is in cache, use their name. Otherwise, default to ID.
        banned_user_name_display = f"User ID {member_id}"
        cached_member = guild.get_member(member_id)
        if cached_member:
            banned_user_name_display = f"{cached_member.name}#{cached_member.discriminator}"
        else:
            # For users not in the server, fetching them by ID is an async operation (bot.fetch_user(id)).
            # To keep this route synchronous, we'll proceed with just the ID for the log message
            # if the user isn't in the guild's member cache. A more complex setup could use an async helper.
            print(f"User {member_id} not found in guild cache. Proceeding to ban by ID. Log will use ID.")

        # Perform the ban
        awaitable_ban = guild.ban(user_object_to_ban, reason=actual_reason, delete_message_seconds=delete_message_seconds)
        future = asyncio.run_coroutine_threadsafe(awaitable_ban, bot.loop)
        future.result() # Wait for ban to complete

        # Log to configured channel
        log_channel_id_str = config.get("TEAM_UPDATES_KANAL_ID")
        if log_channel_id_str:
            try:
                log_channel_id = int(log_channel_id_str)
                channel = bot.get_channel(log_channel_id)
                if channel and isinstance(channel, discordpy.TextChannel):
                    msg = f":hammer: **Ban:** {banned_user_name_display} (`{member_id}`) wurde von {action_user_name} vom Server gebannt.\n**Grund:** {actual_reason}"
                    asyncio.run_coroutine_threadsafe(channel.send(msg), bot.loop)
                else:
                    print(f"Warning: Ban log channel {log_channel_id_str} not found or not a text channel.")
            except ValueError:
                print(f"Warning: Invalid format for TEAM_UPDATES_KANAL_ID: {log_channel_id_str}")
            except Exception as e:
                print(f"Error logging ban to channel: {e}")

        return redirect(url_for('members_list', success=f"{banned_user_name_display} has been banned."))
    except ValueError: # Error converting member_id_str to int
        return redirect(url_for('members_list', error="Invalid member ID format for ban."))
    except discordpy.Forbidden: # Bot lacks permissions
        return redirect(url_for('members_list', error="Bot lacks permission to ban this user (check roles and hierarchy)."))
    except discordpy.HTTPException as http_err: # Specific Discord API errors
        print(f"Discord API Error during ban: {http_err}")
        return redirect(url_for('members_list', error=f"Discord API error during ban: {http_err.text} (Code: {http_err.status})"))
    except Exception as e: # Other unexpected errors
        print(f"Unexpected error during ban: {e}")
        return redirect(url_for('members_list', error=f"An unexpected error occurred during ban: {type(e).__name__}"))



# Global flag to ensure bot thread starts only once
bot_started_flag = False

@app.before_request
def start_bot_once():
    global bot_started_flag
    if not bot_started_flag and bot_thread:
        if config.get('DISCORD_BOT_TOKEN'):
            print("Starting Discord bot thread from before_request...")
            bot_thread.start()
            bot_started_flag = True
        else:
            print("DISCORD_BOT_TOKEN not found, bot thread will not be started.")
            # To prevent re-checking every time if no token
            bot_started_flag = True # Mark as "checked" even if not started


if __name__ == '__main__':
    # Note: In a production environment, use a WSGI server like Gunicorn or Waitress
    # The bot thread should ideally be managed by the WSGI server or a process manager in production.
    # For development with Flask's built-in server, starting it here or via before_request is okay.
    # If not started by before_request (e.g. if no requests come in immediately), start it here.
    if not bot_started_flag and bot_thread and config.get('DISCORD_BOT_TOKEN'):
         print("Starting Discord bot thread from __main__...")
         bot_thread.start()
         bot_started_flag = True

    app.run(debug=True, port=5000)
