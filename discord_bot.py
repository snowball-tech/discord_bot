import discord
from openai import OpenAI
import re
import os
from dotenv import load_dotenv
import logging
from typing import List, Optional
import pprint

load_dotenv()
logging.basicConfig(level=logging.INFO)
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

openai_client = OpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

discord_client = discord.Client(intents=intents)

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

async def handle_dm_summarize(message: discord.Message) -> None:
    channel_name = message.content.split('#', 1)[1].strip()
    found = False
    for guild in discord_client.guilds:
        matches = []
        for ch in guild.text_channels:
            if channel_name.lower() in clean_channel_name(ch.name):
                matches.append(ch)
        if len(matches) == 1:
            channel = matches[0]
            placeholder = await message.channel.send(f"Recherche et résumé des derniers messages de #{channel.name}...")
            messages = []
            try:
                async for msg in channel.history(limit=40):
                    if not msg.author.bot:
                        messages.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M')}] {msg.author.display_name} : {msg.content}")
            except Exception as e:
                logging.error(f"Error accessing messages in channel {channel.name}: {e}")
                await placeholder.edit(content="Désolé, je n'ai pas pu accéder aux messages de ce channel. Veuillez réessayer plus tard.")
                return
            if not messages:
                await placeholder.edit(content="Il n'y a pas de messages récents à résumer dans ce channel.")
                return
            logging.info(f"Summarizing {len(messages)} messages from channel {channel.name}")
            summary_message = summarize_messages(messages, channel.name)
            await placeholder.edit(content=summary_message)
            found = True
            break
        elif len(matches) > 1:
            logging.info(f"Plusieurs channels correspondent à '{channel_name}': {[ch.name for ch in matches]}")
            await message.channel.send(
                f"Plusieurs channels correspondent à '{channel_name}': " +
                ", ".join(f"#{ch.name}" for ch in matches) +
                ". Soyez plus précis."
            )
            found = True
            break
    if not found:
        logging.info(f"Pas de channel correspondant à '{channel_name}'")
        await message.channel.send(f"Pas de channel correspondant à '{channel_name}'.")

async def handle_channel_summarize(message: discord.Message) -> None:
    placeholder = await message.channel.send("Recherche et résumé des derniers messages...")
    messages = []
    try:
        async for msg in message.channel.history(limit=40):
            if not msg.author.bot:
                messages.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M')}] {msg.author.display_name} : {msg.content}")
    except Exception as e:
        logging.error(f"Error accessing messages in channel {message.channel.name}: {e}")
        await placeholder.edit(content="Désolé, je n'ai pas pu accéder aux messages de ce channel. Veuillez réessayer plus tard.")
        return
    if not messages:
        await placeholder.edit(content="Il n'y a pas de messages récents à résumer dans ce channel.")
        return
    if isinstance(message.channel, discord.DMChannel):
        channel_name = 'DM'
    else:
        channel_name = message.channel.name
    logging.info(f"Résumé des {len(messages)} messages de {channel_name}")
    summary_message = summarize_messages(messages)
    await placeholder.edit(content=summary_message)

@discord_client.event
async def on_ready() -> None:
    logging.info(f'Logged in as {discord_client.user}')

@discord_client.event
async def on_message(message: discord.Message) -> None:
    if message.author == discord_client.user:
        return
    # --- DM: Summarize a specific channel with fuzzy search ---
    if message.guild is None and message.content.startswith('!summarize #'):
        await handle_dm_summarize(message)
    # --- In-channel: Summarize the current channel ---
    elif message.guild is not None and message.content.startswith('!summarize'):
        await handle_channel_summarize(message)

discord_client.run(DISCORD_TOKEN)