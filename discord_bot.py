import discord
from openai import OpenAI
import re
import os
from dotenv import load_dotenv
import logging
from typing import List, Optional
import pprint
from discord.ext import commands
from discord import app_commands
import datetime
from supabase import create_client, Client

load_dotenv()
logging.basicConfig(level=logging.INFO)
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True  # <--- This is important!

bot = commands.Bot(command_prefix="!", intents=intents)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def clean_channel_name(name):
    # Remove emojis and special symbols, keep only letters, numbers, dashes, and underscores
    return re.sub(r'[^\w\-]', '', name).lower()

def summarize_messages(messages: List[str], channel_name: Optional[str] = None) -> str:
    MAX_CHARS = 20000
    text = "\n\n".join(reversed(messages))
    if len(text) > MAX_CHARS:
        text = text[-MAX_CHARS:]
        truncated = True
    else:
        truncated = False
    prompt = (
    "Tu es un assistant intelligent chargé de résumer une conversation sur un canal Discord communautaire.\n\n"
    "Ta mission est de condenser les messages suivants en français, en extrayant les informations essentielles :\n"
    "- Résume les échanges par **idée ou discussion**, pas par message.\n"
    "- Identifie les **thèmes abordés** si possible (ex : plateformes, outils, critiques…)\n"
    "- Ignore les blagues, emojis, réactions sans fond.\n"
    "- Regroupe les propos similaires de différents membres.\n"
    "- Utilise des bullet points clairs. Si plusieurs sujets, regroupe sous des titres en gras.\n"
    "- Ne rédige pas plus de 8 à 10 bullet points. Regroupe ou coupe si nécessaire.\n"
    "- Coupe proprement, ne laisse pas de phrases incomplètes.\n"
    "- Ne donne ni intro ni conclusion.\n\n"
    f"Voici la conversation :\n\n{text}\n\nRésumé :"
    )

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.5,
        )

        logging.debug("=== OpenAI response (model_dump) ===")
        pprint.pprint(response.model_dump())

        finish_reason = getattr(response.choices[0], "finish_reason", None)
        if finish_reason == "length":
            logging.warning("⚠️ Résumé tronqué à cause de la limite max_tokens.")
        elif finish_reason:
            logging.info(f"✅ Résumé terminé normalement (reason: {finish_reason}).")
        else:
            logging.info("ℹ️ Aucune information de finish_reason fournie par l'API.")

        summary = response.choices[0].message.content.strip()

        if summary.endswith("…") or summary.endswith("...") or summary.endswith("•"):
            summary = re.sub(r'• .*?$', '', summary, flags=re.DOTALL).strip()

        logging.info("=== Résumé ===")
        logging.info(summary)
    except Exception as e:
        logging.error(f"Error with OpenAI API: {e}")
        summary = f"Error with OpenAI API: {e}"
    if channel_name:
        summary_message = f"**Résumé de #{channel_name}{' (last messages only)' if truncated else ''}:**\n{summary}"
    else:
        summary_message = f"**Résumé{' (last messages only)' if truncated else ''}:**\n{summary}"
    return summary_message

def log_usage(user, channel, guild):
    with open("usage.log", "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} | user={user} | channel={channel} | guild={guild}\n")

def log_error(error, user, channel, guild):
    with open("errors.log", "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} | error={error} | user={user} | channel={channel} | guild={guild}\n")

def log_usage_to_supabase(user, user_id, channel, channel_id):
    data = {
        "timestamp": datetime.datetime.now().isoformat(),
        "user_id": str(user_id),
        "user_name": user,
        "channel_id": str(channel_id),
        "channel_name": channel,
    }
    try:
        supabase.table("bot_usage").insert(data).execute()
    except Exception as e:
        logging.error(f"Failed to log usage to Supabase: {e}")

# Autocomplete function for channel selection
async def channel_autocomplete(interaction: discord.Interaction, current: str):
    channels = []
    # If in a server, only show channels from that server
    if interaction.guild:
        guilds = [interaction.guild]
    else:
        # In DM: show channels from all guilds where the user is a member
        guilds = []
        for guild in bot.guilds:
            member = guild.get_member(interaction.user.id)
            if member:
                guilds.append(guild)
    print(f"Autocomplete called by user {interaction.user} in guilds: {[g.name for g in guilds]}")
    for guild in guilds:
        member = guild.get_member(interaction.user.id)
        if not member:
            continue
        for channel in guild.text_channels:
            if not channel.permissions_for(member).view_channel:
                continue
            if not current or current.lower() in channel.name.lower():
                # Show the guild name for clarity in DMs
                label = f"#{channel.name} ({guild.name})"
                channels.append(app_commands.Choice(name=label, value=str(channel.id)))
    print(f"Channels found: {[c.name for c in channels]}")
    return channels[:25]

@bot.tree.command(name="summarize", description="Résume un canal Discord")
@app_commands.describe(channel="Choisissez le canal à résumer")
@app_commands.autocomplete(channel=channel_autocomplete)
async def summarize(interaction: discord.Interaction, channel: str):
    await interaction.response.defer(thinking=True)  # <-- This tells Discord you're working
    # Find the channel by ID
    channel_obj = None
    for guild in bot.guilds:
        ch = discord.utils.get(guild.text_channels, id=int(channel))
        if ch:
            channel_obj = ch
            break
    if not channel_obj:
        await interaction.response.send_message("Canal introuvable.", ephemeral=True)
        return

    user = interaction.user.display_name
    user_id = interaction.user.id
    # log_usage(user, channel_obj.name, guild)  # (optional: remove this if you only want Supabase logging)
    log_usage_to_supabase(user, user_id, channel_obj.name, channel_obj.id)

    # Fetch last 40 messages (excluding bots)
    messages = []
    try:
        async for msg in channel_obj.history(limit=40):
            if not msg.author.bot:
                messages.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M')}] {msg.author.display_name} : {msg.content}")
    except discord.errors.Forbidden:
        await interaction.followup.send("Je n'ai pas accès à ce canal.", ephemeral=True)
        return

    if not messages:
        await interaction.response.send_message("Aucun message récent à résumer.", ephemeral=True)
        return

    summary = summarize_messages(messages, channel_obj.name)
    await interaction.followup.send(summary, ephemeral=False)

@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user}')
    logging.info('==== Slash command version running! ====')
    try:
        synced = await bot.tree.sync()
        logging.info(f"Slash commands synchronisées: {len(synced)}")
    except Exception as e:
        logging.error(f"Erreur de sync: {e}")

bot.run(DISCORD_TOKEN)