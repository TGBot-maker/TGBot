import traceback
try:
    import discord
    from discord.ext import commands, tasks
    from dotenv import load_dotenv
    from flask import Flask
    from PIL import Image, ImageDraw, ImageFont
    import requests
    print("✅ All imports successful")
except Exception as e:
    print(f"❌ IMPORT ERROR: {e}")
    traceback.print_exc()
import os
import threading
import asyncio
import functools
from datetime import datetime, timedelta
import json
import random

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from flask import Flask

from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO


# =============== FLASK WEB SERVER (FOR RENDER) ===============

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


# =============== DISCORD BOT SETUP ===============

load_dotenv()
TOKEN = os.getenv("TOKEN")

# Fix for asyncio on Linux servers (like Render)
import sys
if sys.platform != "win32":
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# =============== RATE LIMIT HANDLER ===============
# Patches the Discord HTTP client to automatically retry on 429 instead of crashing

_original_request = discord.http.HTTPClient.request

async def _patched_request(self, *args, **kwargs):
    while True:
        try:
            return await _original_request(self, *args, **kwargs)
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = float(e.response.headers.get("Retry-After", 5))
                print(f"⚠️  Rate limited by Discord. Retrying in {retry_after:.1f}s...")
                await asyncio.sleep(retry_after + 0.5)
            else:
                raise

discord.http.HTTPClient.request = _patched_request


# =============== EVENT STORAGE ===============

EVENTS_FILE = "events.json"

def load_events():
    if os.path.exists(EVENTS_FILE):
        with open(EVENTS_FILE, "r") as f:
            return json.load(f)
    return []

def save_events(events):
    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=2)

events = load_events()


# =============== XP / LEVEL STORAGE ===============

XP_FILE = "xp.json"

def load_xp():
    if os.path.exists(XP_FILE):
        with open(XP_FILE, "r") as f:
            return json.load(f)
    return {}

def save_xp(data):
    with open(XP_FILE, "w") as f:
        json.dump(data, f, indent=2)

xp_data = load_xp()
xp_cooldowns = {}       # user_id -> last message timestamp
xp_save_counter = 0     # batch disk writes every 10 XP gains


# =============== ECONOMY STORAGE ===============

ECONOMY_FILE = "economy.json"

def load_economy():
    if os.path.exists(ECONOMY_FILE):
        with open(ECONOMY_FILE, "r") as f:
            return json.load(f)
    return {}

def save_economy(data):
    with open(ECONOMY_FILE, "w") as f:
        json.dump(data, f, indent=2)

economy_data = load_economy()
daily_cooldowns = {}    # user_id -> last daily claim timestamp


# =============== WARNINGS STORAGE ===============

WARNINGS_FILE = "warnings.json"

def load_warnings():
    if os.path.exists(WARNINGS_FILE):
        with open(WARNINGS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_warnings(data):
    with open(WARNINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)

warnings_data = load_warnings()


# =============== INTRUDER ALERT SYSTEM ===============

intruder_alert_enabled = False
intruder_count = 0

# =============== IMAGE POLL STORAGE ===============

active_polls = {}


# =============== HELPER FUNCTIONS ===============

def get_level(xp):
    return int((xp / 100) ** 0.5)

def xp_for_next_level(level):
    return ((level + 1) ** 2) * 100

def get_economy(user_id):
    uid = str(user_id)
    if uid not in economy_data:
        economy_data[uid] = {"coins": 0, "bank": 0}
    return economy_data[uid]

def get_xp_data(user_id):
    uid = str(user_id)
    if uid not in xp_data:
        xp_data[uid] = {"xp": 0, "level": 0, "messages": 0}
    return xp_data[uid]


# =============== EVENT CHECKER TASK ===============
# Runs every 2 minutes instead of 1 to reduce API call frequency

@tasks.loop(minutes=2)
async def check_events():
    global events
    now = datetime.now()

    for event in events[:]:
        event_time = datetime.fromisoformat(event["time"])
        reminder_time = event_time - timedelta(minutes=10)

        if not event.get("reminder_sent", False) and now >= reminder_time and now < event_time:
            channel = bot.get_channel(event["channel_id"])
            if channel:
                mention = event["mention"]
                await channel.send(
                    f"⏰ **Reminder:** {mention} - Event **'{event['name']}'** starts in 10 minutes!"
                )
                event["reminder_sent"] = True
                save_events(events)
            await asyncio.sleep(1)  # small delay between sends

        if now >= event_time and not event.get("event_triggered", False):
            channel = bot.get_channel(event["channel_id"])
            if channel:
                mention = event["mention"]
                await channel.send(
                    f"🔔 **EVENT NOW:** {mention} - **'{event['name']}'** is starting!"
                )
                event["event_triggered"] = True

                if event.get("repeat_days"):
                    next_time = event_time + timedelta(days=event["repeat_days"])
                    new_event = event.copy()
                    new_event["time"] = next_time.isoformat()
                    new_event["reminder_sent"] = False
                    new_event["event_triggered"] = False
                    events.append(new_event)
                    await channel.send(
                        f"📅 Next occurrence scheduled for: {next_time.strftime('%Y-%m-%d %H:%M')}"
                    )

                save_events(events)
            await asyncio.sleep(1)  # small delay between sends

        if event.get("event_triggered", False) and not event.get("repeat_days"):
            if now > event_time + timedelta(hours=24):
                events.remove(event)
                save_events(events)


@check_events.before_loop
async def before_check_events():
    await bot.wait_until_ready()


# =============== WELCOME FEATURE ===============

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    check_events.start()
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="over the server 👀")
    )


@bot.event
async def on_member_join(member):
    try:
        template = Image.open("image.png").convert("RGBA")
        avatar_url = member.display_avatar.url

        # Run blocking HTTP call in a thread so it doesn't block the event loop
        response = await asyncio.get_event_loop().run_in_executor(
            None, functools.partial(requests.get, avatar_url)
        )
        avatar_bytes = response.content

        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((140, 140))
        template.paste(avatar, (390, 28), avatar)
        draw = ImageDraw.Draw(template)
        font = ImageFont.truetype("pokemon-gb.ttf", 38)
        draw.text((190, 469), member.name, font=font, fill=(0, 0, 0))
        output_path = "welcome.png"
        template.save(output_path)

        channel = member.guild.system_channel
        if channel:
            await channel.send(file=discord.File(output_path))

    except Exception as e:
        print(f"Welcome image error: {e}")
        channel = member.guild.system_channel
        if channel:
            embed = discord.Embed(
                title=f"👋 Welcome to {member.guild.name}!",
                description=f"Hey {member.mention}, glad you're here! 🎉",
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)


# =============== ON MESSAGE — XP + INTRUDER ===============

@bot.event
async def on_message(message):
    global intruder_alert_enabled, intruder_count, xp_save_counter

    if message.author.bot:
        await bot.process_commands(message)
        return

    # Intruder alert system
    if intruder_alert_enabled and message.guild:
        intruder_role = discord.utils.get(message.guild.roles, name="Intruder")
        if intruder_role and intruder_role in message.author.roles:
            await message.delete()
            intruder_count += 1

            alerts = [
                f"🚨🚨🚨 **RED ALERT!** 🚨🚨🚨\n\nAn INTRUDER has been detected!\nMessage from **{message.author.mention}** deleted and sent to the SHADOW REALM™\nTotal intruders caught: **{intruder_count}**",
                f"📛 **INTRUDER DETECTED** 📛\n\n*WOOP WOOP* 🚨\nMessage from {message.author.mention} has been VAPORIZED!\nIntruders neutralized: {intruder_count}",
                f"🛑 **HALT!** 🛑\n\n{message.author.mention}'s message was yeeted into oblivion\nIntruders caught: **{intruder_count}**",
                f"😤 **AN INTRUDER?!** 😤\n\n{message.author.mention} tried to pull a fast one...\nNOPE! Message OBLITERATED! Total caught: {intruder_count} 👑"
            ]
            await message.channel.send(random.choice(alerts))
            await bot.process_commands(message)
            return

    # XP gain (1 minute cooldown per user)
    if message.guild:
        uid = str(message.author.id)
        now = datetime.now()
        last = xp_cooldowns.get(uid)

        if last is None or (now - last).total_seconds() >= 60:
            xp_cooldowns[uid] = now
            data = get_xp_data(uid)
            gained = random.randint(15, 25)
            data["xp"] += gained
            data["messages"] += 1
            old_level = data["level"]
            new_level = get_level(data["xp"])
            data["level"] = new_level

            # Batch disk writes — only save every 10 XP gains
            xp_save_counter += 1
            if xp_save_counter >= 10:
                save_xp(xp_data)
                xp_save_counter = 0

            if new_level > old_level:
                save_xp(xp_data)  # always save immediately on level up
                xp_save_counter = 0
                await message.channel.send(
                    f"🎉 **LEVEL UP!** {message.author.mention} just reached **Level {new_level}**! 🚀"
                )

    await bot.process_commands(message)


# =============== EVENT COMMANDS ===============

@bot.command(name="addevent")
async def add_event(ctx, event_name: str, date: str, time: str, mention: str, repeat_days: int = 0):
    """Add a new event. Usage: !addevent "Name" YYYY-MM-DD HH:MM @role [repeat_days]"""
    try:
        event_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        if event_datetime <= datetime.now():
            await ctx.send("❌ Event time must be in the future!")
            return

        event = {
            "name": event_name,
            "time": event_datetime.isoformat(),
            "channel_id": ctx.channel.id,
            "mention": mention,
            "repeat_days": repeat_days if repeat_days > 0 else None,
            "reminder_sent": False,
            "event_triggered": False,
            "created_by": str(ctx.author)
        }

        events.append(event)
        save_events(events)

        repeat_info = f" (repeats every {repeat_days} days)" if repeat_days > 0 else ""
        await ctx.send(
            f"✅ Event added!\n**Event:** {event_name}\n**Time:** {event_datetime.strftime('%Y-%m-%d %H:%M')}\n"
            f"**Mention:** {mention}\n**Reminder:** 10 minutes before{repeat_info}"
        )
    except ValueError:
        await ctx.send("❌ Invalid format! Use: `!addevent \"Name\" YYYY-MM-DD HH:MM @mention [days]`")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


@bot.command(name="listevents")
async def list_events(ctx):
    """List all upcoming events"""
    now = datetime.now()
    upcoming = sorted(
        [e for e in events if datetime.fromisoformat(e["time"]) > now],
        key=lambda x: x["time"]
    )

    if not upcoming:
        await ctx.send("📅 No upcoming events.")
        return

    embed = discord.Embed(title="📅 Upcoming Events", color=discord.Color.blue())
    for i, event in enumerate(upcoming[:10], 1):
        event_time = datetime.fromisoformat(event["time"])
        time_until = event_time - now
        repeat_info = f"\n🔁 Repeats every {event['repeat_days']} days" if event.get("repeat_days") else ""
        embed.add_field(
            name=f"{i}. {event['name']}",
            value=(
                f"⏰ {event_time.strftime('%Y-%m-%d %H:%M')}\n"
                f"👥 {event['mention']}\n"
                f"⏳ In {time_until.days}d {time_until.seconds//3600}h {(time_until.seconds//60)%60}m"
                f"{repeat_info}"
            ),
            inline=False
        )
    await ctx.send(embed=embed)


@bot.command(name="deleteevent")
async def delete_event(ctx, event_index: int):
    """Delete an event by number. Use !listevents to see numbers."""
    now = datetime.now()
    upcoming = sorted(
        [e for e in events if datetime.fromisoformat(e["time"]) > now],
        key=lambda x: x["time"]
    )

    if event_index < 1 or event_index > len(upcoming):
        await ctx.send("❌ Invalid event number! Use `!listevents` first.")
        return

    event_to_delete = upcoming[event_index - 1]
    events.remove(event_to_delete)
    save_events(events)
    await ctx.send(f"✅ Deleted event: **'{event_to_delete['name']}'**")


@bot.command(name="eventhelp")
async def event_help(ctx):
    """Show event command help"""
    embed = discord.Embed(title="🎯 Event System Help", color=discord.Color.green())
    embed.add_field(name="!addevent", value='`!addevent "Name" YYYY-MM-DD HH:MM @mention [repeat_days]`', inline=False)
    embed.add_field(name="!listevents", value="View all upcoming events with countdowns", inline=False)
    embed.add_field(name="!deleteevent", value="`!deleteevent 1` — delete by list number", inline=False)
    embed.add_field(name="⏰ Reminders", value="10 min before + at event time. Repeating events auto-reschedule.", inline=False)
    await ctx.send(embed=embed)


# =============== INTRUDER ALERT COMMANDS ===============

@bot.command(name="toggleintruder")
@commands.has_permissions(manage_messages=True)
async def toggle_intruder(ctx):
    """Toggle the intruder alert system (requires Manage Messages)"""
    global intruder_alert_enabled, intruder_count

    intruder_alert_enabled = not intruder_alert_enabled
    intruder_count = 0

    if intruder_alert_enabled:
        embed = discord.Embed(title="🚨 INTRUDER ALERT ACTIVATED 🚨", color=discord.Color.red())
        embed.add_field(name="Status", value="🔴 LIVE", inline=False)
        embed.add_field(name="Action", value="Messages from the 'Intruder' role will be deleted", inline=False)
    else:
        embed = discord.Embed(title="✅ INTRUDER ALERT DEACTIVATED", color=discord.Color.green())
        embed.add_field(name="Status", value="🟢 OFFLINE", inline=False)
        embed.add_field(name="Intruders Caught", value=f"⚡ {intruder_count}", inline=False)

    await ctx.send(embed=embed)


# =============== IMAGE POLL SYSTEM ===============

class ImagePollView(discord.ui.View):
    def __init__(self, title, image_urls):
        super().__init__(timeout=None)
        self.title = title
        self.options = [{"img": url, "votes": 0} for url in image_urls]
        self.voters = {}
        self.message = None
        self.closed = False

        for idx in range(len(image_urls)):
            self.add_item(ImagePollButton(idx))


class ImagePollButton(discord.ui.Button):
    def __init__(self, index):
        super().__init__(
            label=f"{index + 1}️⃣",
            style=discord.ButtonStyle.primary,
            custom_id=f"imgpoll_{index}"
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: ImagePollView = self.view

        if view.closed:
            return await interaction.response.send_message("❌ This poll is already closed.", ephemeral=True)

        uid = interaction.user.id
        prev_vote = view.voters.get(uid)

        if prev_vote == self.index:
            view.voters.pop(uid)
            view.options[self.index]["votes"] -= 1
            msg = f"Removed your vote for **Option {self.index + 1}**."
        else:
            if prev_vote is not None:
                view.options[prev_vote]["votes"] -= 1
            view.voters[uid] = self.index
            view.options[self.index]["votes"] += 1
            msg = f"Voted for **Option {self.index + 1}**! ✅"

        await update_image_poll(view)
        await interaction.response.send_message(msg, ephemeral=True)


async def update_image_poll(view: ImagePollView):
    embeds = []
    for i, opt in enumerate(view.options):
        emb = discord.Embed(description=f"**Option {i + 1}**")
        emb.set_image(url=opt["img"])
        emb.set_footer(text=f"Votes: {opt['votes']}")
        embeds.append(emb)
    await view.message.edit(embeds=embeds, view=view)


async def close_image_poll(message_id, minutes):
    await asyncio.sleep(minutes * 60)

    view = active_polls.get(message_id)
    if not view:
        return

    view.closed = True
    for item in view.children:
        item.disabled = True

    max_votes = max(o["votes"] for o in view.options)
    winners = [f"Option {i + 1}" for i, o in enumerate(view.options) if o["votes"] == max_votes]
    result = "🏆 Winner: " + ", ".join(winners) if max_votes > 0 else "No votes cast."

    await view.message.edit(content=f"**{view.title}** — Poll closed! {result}", view=view)


@bot.command(name="imagepoll")
async def image_poll(ctx, title: str, img1: str, img2: str, img3: str = None, img4: str = None, duration: int = None):
    """Create an image poll. Usage: !imagepoll "Title" url1 url2 [url3] [url4] [duration_minutes]"""
    image_urls = [img1, img2]
    if img3:
        image_urls.append(img3)
    if img4:
        image_urls.append(img4)

    embeds = []
    for i, url in enumerate(image_urls):
        emb = discord.Embed(description=f"**Option {i + 1}**")
        emb.set_image(url=url)
        emb.set_footer(text="Votes: 0")
        embeds.append(emb)

    view = ImagePollView(title, image_urls)
    msg = await ctx.send(content=f"🗳️ **{title}**", embeds=embeds, view=view)
    view.message = msg
    active_polls[msg.id] = view

    if duration:
        bot.loop.create_task(close_image_poll(msg.id, duration))


# =============== XP / LEVELING COMMANDS ===============

@bot.command(name="rank")
async def rank(ctx, member: discord.Member = None):
    """Check your rank or someone else's. Usage: !rank [@member]"""
    member = member or ctx.author
    data = get_xp_data(str(member.id))

    level = data["level"]
    xp = data["xp"]
    next_lvl_xp = xp_for_next_level(level)
    progress = min(int((xp / next_lvl_xp) * 20), 20)
    bar = "█" * progress + "░" * (20 - progress)

    embed = discord.Embed(title=f"📊 {member.display_name}'s Rank", color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=f"**{level}**", inline=True)
    embed.add_field(name="XP", value=f"**{xp}** / {next_lvl_xp}", inline=True)
    embed.add_field(name="Messages", value=f"**{data['messages']}**", inline=True)
    embed.add_field(name="Progress", value=f"`[{bar}]`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard(ctx):
    """Show the top 10 most active members"""
    if not xp_data:
        await ctx.send("No XP data yet!")
        return

    sorted_users = sorted(xp_data.items(), key=lambda x: x[1].get("xp", 0), reverse=True)[:10]
    embed = discord.Embed(title="🏆 Server Leaderboard", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]

    for i, (uid, data) in enumerate(sorted_users):
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else f"User {uid}"
        medal = medals[i] if i < 3 else f"**#{i+1}**"
        embed.add_field(
            name=f"{medal} {name}",
            value=f"Level {data.get('level', 0)} — {data.get('xp', 0)} XP",
            inline=False
        )

    await ctx.send(embed=embed)


# =============== ECONOMY COMMANDS ===============

@bot.command(name="balance", aliases=["bal"])
async def balance(ctx, member: discord.Member = None):
    """Check coin balance. Usage: !balance [@member]"""
    member = member or ctx.author
    data = get_economy(str(member.id))
    embed = discord.Embed(title=f"💰 {member.display_name}'s Balance", color=discord.Color.gold())
    embed.add_field(name="Wallet", value=f"🪙 **{data['coins']}** coins", inline=True)
    embed.add_field(name="Bank", value=f"🏦 **{data['bank']}** coins", inline=True)
    embed.add_field(name="Total", value=f"💎 **{data['coins'] + data['bank']}** coins", inline=True)
    await ctx.send(embed=embed)


@bot.command(name="daily")
async def daily(ctx):
    """Claim your daily coins (once every 24 hours)"""
    uid = str(ctx.author.id)
    now = datetime.now()
    last_claim = daily_cooldowns.get(uid)

    if last_claim and (now - last_claim).total_seconds() < 86400:
        remaining = timedelta(seconds=86400) - (now - last_claim)
        hours, rem = divmod(int(remaining.total_seconds()), 3600)
        minutes = rem // 60
        await ctx.send(f"⏳ Already claimed! Come back in **{hours}h {minutes}m**.")
        return

    reward = random.randint(100, 500)
    bonus = 0
    if last_claim and (now - last_claim).total_seconds() < 172800:
        bonus = random.randint(50, 150)
        reward += bonus

    data = get_economy(uid)
    data["coins"] += reward
    save_economy(economy_data)
    daily_cooldowns[uid] = now

    embed = discord.Embed(title="💰 Daily Reward!", color=discord.Color.green())
    embed.add_field(name="Coins Earned", value=f"🪙 **{reward}**", inline=True)
    if bonus > 0:
        embed.add_field(name="Streak Bonus", value=f"🔥 +{bonus}", inline=True)
    embed.add_field(name="New Balance", value=f"💎 {data['coins']}", inline=True)
    embed.set_footer(text="Come back tomorrow for more!")
    await ctx.send(embed=embed)


@bot.command(name="work")
async def work(ctx):
    """Work to earn coins (1 hour cooldown)"""
    uid = str(ctx.author.id)
    work_key = f"work_{uid}"
    now = datetime.now()
    last_work = xp_cooldowns.get(work_key)

    if last_work and (now - last_work).total_seconds() < 3600:
        remaining = 3600 - (now - last_work).total_seconds()
        minutes = int(remaining // 60)
        await ctx.send(f"😅 You're tired! Rest for **{minutes} more minutes**.")
        return

    xp_cooldowns[work_key] = now
    jobs = [
        ("🧑‍💻 You coded a new feature", 80, 200),
        ("🎨 You designed a logo", 60, 180),
        ("🚚 You delivered packages", 50, 150),
        ("🍕 You delivered pizza", 40, 120),
        ("🏗️ You fixed some bugs", 70, 190),
        ("📝 You wrote a blog post", 45, 130),
        ("🎵 You busked on the street", 30, 100),
        ("🌿 You mowed some lawns", 35, 110),
    ]

    job, min_earn, max_earn = random.choice(jobs)
    earned = random.randint(min_earn, max_earn)
    data = get_economy(uid)
    data["coins"] += earned
    save_economy(economy_data)

    await ctx.send(f"{job} and earned **{earned} coins**! 💰\nNew balance: 🪙 {data['coins']}")


@bot.command(name="deposit", aliases=["dep"])
async def deposit(ctx, amount: str):
    """Deposit coins into your bank. Usage: !deposit 500 or !deposit all"""
    uid = str(ctx.author.id)
    data = get_economy(uid)

    if amount.lower() == "all":
        amount = data["coins"]
    else:
        try:
            amount = int(amount)
        except ValueError:
            await ctx.send("❌ Use a number or 'all'.")
            return

    if amount <= 0 or amount > data["coins"]:
        await ctx.send(f"❌ You only have **{data['coins']}** coins in your wallet!")
        return

    data["coins"] -= amount
    data["bank"] += amount
    save_economy(economy_data)
    await ctx.send(f"🏦 Deposited **{amount} coins**!\nWallet: {data['coins']} | Bank: {data['bank']}")


@bot.command(name="withdraw", aliases=["with"])
async def withdraw(ctx, amount: str):
    """Withdraw coins from your bank. Usage: !withdraw 200 or !withdraw all"""
    uid = str(ctx.author.id)
    data = get_economy(uid)

    if amount.lower() == "all":
        amount = data["bank"]
    else:
        try:
            amount = int(amount)
        except ValueError:
            await ctx.send("❌ Use a number or 'all'.")
            return

    if amount <= 0 or amount > data["bank"]:
        await ctx.send(f"❌ You only have **{data['bank']}** coins in your bank!")
        return

    data["bank"] -= amount
    data["coins"] += amount
    save_economy(economy_data)
    await ctx.send(f"💸 Withdrew **{amount} coins**!\nWallet: {data['coins']} | Bank: {data['bank']}")


@bot.command(name="gamble")
async def gamble(ctx, amount: int):
    """Gamble your coins! Usage: !gamble 500"""
    uid = str(ctx.author.id)
    data = get_economy(uid)

    if amount <= 0:
        await ctx.send("❌ Bet must be positive!")
        return
    if amount > data["coins"]:
        await ctx.send(f"❌ You only have **{data['coins']}** coins!")
        return
    if amount > 10000:
        await ctx.send("❌ Max bet is 10,000 coins!")
        return

    roll = random.random()
    if roll > 0.45:
        data["coins"] -= amount
        result = f"💸 You lost **{amount} coins**! Better luck next time..."
        color = discord.Color.red()
    else:
        data["coins"] += amount
        result = f"🎰 You won **{amount * 2} coins**! You're on fire! 🔥"
        color = discord.Color.green()

    save_economy(economy_data)
    embed = discord.Embed(title="🎰 Gamble Result", description=result, color=color)
    embed.add_field(name="New Balance", value=f"🪙 {data['coins']}", inline=False)
    await ctx.send(embed=embed)


# =============== MODERATION COMMANDS ===============

@bot.command(name="warn")
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason given"):
    """Warn a member. Usage: !warn @member reason"""
    uid = str(member.id)
    if uid not in warnings_data:
        warnings_data[uid] = []

    warnings_data[uid].append({
        "reason": reason,
        "by": str(ctx.author),
        "time": datetime.now().isoformat()
    })
    save_warnings(warnings_data)

    count = len(warnings_data[uid])
    embed = discord.Embed(title="⚠️ Member Warned", color=discord.Color.orange())
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.add_field(name="Total Warnings", value=f"**{count}**", inline=True)
    embed.add_field(name="Warned by", value=ctx.author.mention, inline=True)
    await ctx.send(embed=embed)

    try:
        await member.send(f"⚠️ You've been warned in **{ctx.guild.name}**!\nReason: {reason}\nTotal warnings: {count}")
    except discord.Forbidden:
        pass


@bot.command(name="warnings")
async def show_warnings(ctx, member: discord.Member = None):
    """Show warnings for a member. Usage: !warnings [@member]"""
    member = member or ctx.author
    uid = str(member.id)
    user_warnings = warnings_data.get(uid, [])

    if not user_warnings:
        await ctx.send(f"✅ **{member.display_name}** has no warnings!")
        return

    embed = discord.Embed(title=f"⚠️ Warnings for {member.display_name}", color=discord.Color.orange())
    for i, w in enumerate(user_warnings[-5:], 1):
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {w['reason']}\n**By:** {w['by']}\n**When:** {w['time'][:10]}",
            inline=False
        )
    await ctx.send(embed=embed)


@bot.command(name="clearwarnings")
@commands.has_permissions(administrator=True)
async def clear_warnings(ctx, member: discord.Member):
    """Clear all warnings for a member (Admin only). Usage: !clearwarnings @member"""
    uid = str(member.id)
    warnings_data[uid] = []
    save_warnings(warnings_data)
    await ctx.send(f"✅ Cleared all warnings for {member.mention}!")


@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    """Bulk delete messages. Usage: !purge 10"""
    if amount < 1 or amount > 100:
        await ctx.send("❌ Amount must be between 1 and 100.")
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f"🗑️ Deleted **{len(deleted) - 1}** messages!")
    await asyncio.sleep(3)
    await msg.delete()


# =============== FUN COMMANDS ===============

@bot.command(name="8ball")
async def eight_ball(ctx, *, question: str):
    """Ask the magic 8-ball. Usage: !8ball Will I win today?"""
    responses = [
        "🎱 It is certain.", "🎱 Without a doubt.", "🎱 Yes, definitely!",
        "🎱 You may rely on it.", "🎱 As I see it, yes.", "🎱 Most likely.",
        "🎱 Outlook good.", "🎱 Signs point to yes.", "🎱 Reply hazy, try again.",
        "🎱 Ask again later.", "🎱 Better not tell you now.", "🎱 Cannot predict now.",
        "🎱 Don't count on it.", "🎱 My reply is no.", "🎱 My sources say no.",
        "🎱 Outlook not so good.", "🎱 Very doubtful.", "🎱 Absolutely not!"
    ]
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=discord.Color.dark_purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer", value=random.choice(responses), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="roll")
async def roll(ctx, dice: str = "1d6"):
    """Roll dice. Usage: !roll 2d6"""
    try:
        parts = dice.lower().split("d")
        num_dice = int(parts[0]) if parts[0] else 1
        sides = int(parts[1])

        if num_dice < 1 or num_dice > 20 or sides < 2 or sides > 100:
            await ctx.send("❌ Use 1–20 dice with 2–100 sides.")
            return

        rolls = [random.randint(1, sides) for _ in range(num_dice)]
        embed = discord.Embed(title="🎲 Dice Roll", color=discord.Color.blue())
        embed.add_field(name="Dice", value=f"**{dice}**", inline=True)
        embed.add_field(name="Rolls", value=f"{rolls}", inline=True)
        embed.add_field(name="Total", value=f"**{sum(rolls)}**", inline=True)
        await ctx.send(embed=embed)
    except (ValueError, IndexError):
        await ctx.send("❌ Invalid format! Use: `!roll 2d6`")


@bot.command(name="coinflip", aliases=["flip"])
async def coinflip(ctx):
    """Flip a coin"""
    result = random.choice(["Heads 🪙", "Tails 🦅"])
    await ctx.send(f"🪙 **{result}!**")


@bot.command(name="rps")
async def rock_paper_scissors(ctx, choice: str):
    """Play Rock Paper Scissors. Usage: !rps rock"""
    choice = choice.lower()
    options = ["rock", "paper", "scissors"]
    emojis = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}

    if choice not in options:
        await ctx.send("❌ Choose: `rock`, `paper`, or `scissors`!")
        return

    bot_choice = random.choice(options)

    if choice == bot_choice:
        result, color = "It's a tie! 🤝", discord.Color.gold()
    elif (choice == "rock" and bot_choice == "scissors") or \
         (choice == "paper" and bot_choice == "rock") or \
         (choice == "scissors" and bot_choice == "paper"):
        result, color = "You win! 🎉", discord.Color.green()
    else:
        result, color = "I win! 😈", discord.Color.red()

    embed = discord.Embed(title="✊ Rock Paper Scissors", description=result, color=color)
    embed.add_field(name="Your choice", value=emojis[choice], inline=True)
    embed.add_field(name="My choice", value=emojis[bot_choice], inline=True)
    await ctx.send(embed=embed)


@bot.command(name="trivia")
async def trivia(ctx):
    """Answer a random trivia question for coins!"""
    questions = [
        ("What planet is known as the Red Planet?", "mars"),
        ("What is the capital of France?", "paris"),
        ("How many sides does a hexagon have?", "6"),
        ("What is the chemical symbol for water?", "h2o"),
        ("Who wrote Romeo and Juliet?", "shakespeare"),
        ("What is the fastest land animal?", "cheetah"),
        ("How many bones are in the adult human body?", "206"),
        ("What is the largest ocean?", "pacific"),
        ("In what year did World War 2 end?", "1945"),
        ("What gas do plants absorb from the air?", "co2"),
    ]

    q, answer = random.choice(questions)
    embed = discord.Embed(title="🧠 Trivia Time!", description=q, color=discord.Color.teal())
    embed.set_footer(text="You have 30 seconds to answer!")
    await ctx.send(embed=embed)

    def check(m):
        return m.channel == ctx.channel and not m.author.bot

    try:
        msg = await bot.wait_for("message", timeout=30.0, check=check)
        if answer.lower() in msg.content.lower():
            reward = random.randint(50, 150)
            data = get_economy(str(msg.author.id))
            data["coins"] += reward
            save_economy(economy_data)
            await ctx.send(
                f"✅ **{msg.author.display_name}** got it right! The answer was **{answer}**! +{reward} coins 🪙"
            )
        else:
            await ctx.send(f"❌ Wrong! The correct answer was **{answer}**.")
    except asyncio.TimeoutError:
        await ctx.send(f"⏰ Time's up! The answer was **{answer}**.")


@bot.command(name="meme")
async def meme(ctx):
    """Get a random meme from Reddit"""
    subreddits = ["memes", "dankmemes", "me_irl", "funny"]
    sub = random.choice(subreddits)
    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            functools.partial(
                requests.get,
                f"https://www.reddit.com/r/{sub}/random.json",
                headers={"User-agent": "DiscordBot/1.0"},
                timeout=5
            )
        )
        data = response.json()
        post = data[0]["data"]["children"][0]["data"]

        if post.get("over_18"):
            await ctx.send("🔞 Got an NSFW meme, skipping. Try again!")
            return

        embed = discord.Embed(title=post["title"], color=discord.Color.orange())
        embed.set_image(url=post.get("url", ""))
        embed.set_footer(text=f"👍 {post['ups']} | r/{sub}")
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("😅 Couldn't fetch a meme right now. Try again!")


@bot.command(name="poll")
async def text_poll(ctx, *, question: str):
    """Create a quick yes/no poll. Usage: !poll Is pizza the best food?"""
    embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.blue())
    embed.set_footer(text=f"Poll by {ctx.author.display_name}")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    await msg.add_reaction("🤷")


@bot.command(name="avatar")
async def avatar(ctx, member: discord.Member = None):
    """Get someone's avatar. Usage: !avatar [@member]"""
    member = member or ctx.author
    embed = discord.Embed(title=f"🖼️ {member.display_name}'s Avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="serverinfo")
async def server_info(ctx):
    """Show server information"""
    guild = ctx.guild
    embed = discord.Embed(title=f"📋 {guild.name}", color=discord.Color.blurple())
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="Members", value=f"👥 {guild.member_count}", inline=True)
    embed.add_field(name="Channels", value=f"💬 {len(guild.text_channels)} text | 🔊 {len(guild.voice_channels)} voice", inline=True)
    embed.add_field(name="Roles", value=f"🎭 {len(guild.roles)}", inline=True)
    embed.add_field(name="Boosts", value=f"💎 {guild.premium_subscription_count} (Level {guild.premium_tier})", inline=True)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="userinfo")
async def user_info(ctx, member: discord.Member = None):
    """Show user information. Usage: !userinfo [@member]"""
    member = member or ctx.author
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    embed = discord.Embed(title=f"👤 {member.display_name}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="Roles", value=" ".join(roles) if roles else "None", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="say")
@commands.has_permissions(manage_messages=True)
async def say(ctx, *, message: str):
    """Make the bot say something (Mod only). Usage: !say Hello!"""
    await ctx.message.delete()
    await ctx.send(message)


@bot.command(name="embed")
@commands.has_permissions(manage_messages=True)
async def send_embed(ctx, title: str, *, description: str):
    """Send a formatted embed (Mod only). Usage: !embed "Title" Description"""
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    embed.set_footer(text=f"Posted by {ctx.author.display_name}")
    await ctx.message.delete()
    await ctx.send(embed=embed)


# =============== HELP COMMAND ===============

@bot.command(name="help")
async def help_command(ctx, command: str = None):
    """Show all commands or detailed help for one. Usage: !help [command]"""

    command_details = {
        "addevent":       ('!addevent "Name" YYYY-MM-DD HH:MM @mention [repeat_days]', 'Schedule an event with an auto reminder 10 mins before.\nExample: `!addevent "Raid Night" 2026-03-10 20:00 @everyone 7`'),
        "listevents":     ('!listevents', 'Show all upcoming events with countdown timers.'),
        "deleteevent":    ('!deleteevent 2', 'Delete event #2 from the !listevents list.'),
        "rank":           ('!rank [@member]', 'Check your XP, level, and progress bar. Mention someone to check theirs.'),
        "leaderboard":    ('!leaderboard', 'Top 10 most active members by XP. Alias: !lb'),
        "balance":        ('!balance [@member]', 'Check wallet + bank coins. Alias: !bal'),
        "daily":          ('!daily', 'Claim 100–500 free coins once every 24 hours. Consecutive days give a streak bonus.'),
        "work":           ('!work', 'Earn 30–200 coins. 1 hour cooldown.'),
        "deposit":        ('!deposit 500  or  !deposit all', 'Move coins from wallet into your bank. Alias: !dep'),
        "withdraw":       ('!withdraw 200  or  !withdraw all', 'Move coins from bank to wallet. Alias: !with'),
        "gamble":         ('!gamble 500', 'Bet up to 10,000 coins. 45% chance to double, 55% to lose.'),
        "8ball":          ('!8ball Will I win today?', 'Ask the magic 8-ball any yes/no question.'),
        "roll":           ('!roll 2d6', 'Roll dice. Supports 1–20 dice with 2–100 sides. Default: 1d6'),
        "coinflip":       ('!coinflip', 'Flip a coin. Heads or tails. Alias: !flip'),
        "rps":            ('!rps rock', 'Play Rock Paper Scissors. Options: rock / paper / scissors'),
        "trivia":         ('!trivia', 'Answer a trivia question in 30 seconds. Correct answer earns 50–150 coins.'),
        "meme":           ('!meme', 'Fetch a random meme from Reddit.'),
        "poll":           ('!poll Is hot dog a sandwich?', 'Create a quick ✅ ❌ 🤷 reaction poll.'),
        "imagepoll":      ('!imagepoll "Title" url1 url2 [url3] [url4] [duration_mins]', 'Create a poll with images. Users click buttons to vote.'),
        "avatar":         ('!avatar [@member]', "Get a full-size version of someone's avatar."),
        "serverinfo":     ('!serverinfo', 'Show stats about this server.'),
        "userinfo":       ('!userinfo [@member]', 'Show account info, join date, and roles for a member.'),
        "warn":           ('!warn @member reason', '⚠️ Mod only. Warn a member and DM them the reason.'),
        "warnings":       ('!warnings [@member]', 'Show the last 5 warnings for a member.'),
        "clearwarnings":  ('!clearwarnings @member', '🔒 Admin only. Clear all warnings for a member.'),
        "purge":          ('!purge 15', '⚠️ Mod only. Bulk delete up to 100 messages in the channel.'),
        "toggleintruder": ('!toggleintruder', '⚠️ Mod only. Toggle the intruder alert system on/off.'),
        "say":            ('!say Hello everyone!', '⚠️ Mod only. Make the bot send a message (deletes your command).'),
        "embed":          ('!embed "Title" Description here', '⚠️ Mod only. Send a formatted embed message.'),
    }

    if command:
        cmd = command.lower().lstrip("!")
        if cmd not in command_details:
            await ctx.send(f"❓ Unknown command `{cmd}`. Use `!help` to see all commands.")
            return
        usage, description = command_details[cmd]
        embed = discord.Embed(title=f"📖 !{cmd}", color=discord.Color.blurple())
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
        embed.add_field(name="Description", value=description, inline=False)
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(
        title="🤖 Bot Commands",
        description="Use `!help <command>` for detailed usage.\nExample: `!help gamble`",
        color=discord.Color.blurple()
    )
    embed.add_field(name="📅 Events",       value="`addevent` `listevents` `deleteevent`", inline=False)
    embed.add_field(name="📊 XP & Levels",  value="`rank` `leaderboard`", inline=False)
    embed.add_field(name="💰 Economy",      value="`balance` `daily` `work` `deposit` `withdraw` `gamble`", inline=False)
    embed.add_field(name="🎮 Fun",          value="`8ball` `roll` `coinflip` `rps` `trivia` `meme` `poll` `imagepoll`", inline=False)
    embed.add_field(name="👤 Info",         value="`avatar` `serverinfo` `userinfo`", inline=False)
    embed.add_field(name="🛡️ Moderation",  value="`warn` `warnings` `clearwarnings` `purge` `toggleintruder` `say` `embed`", inline=False)
    embed.set_footer(text="⚠️ = requires moderator/admin permissions")
    await ctx.send(embed=embed)


# =============== ERROR HANDLER ===============

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use that command!")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument! Use `!help {ctx.command}` to see correct usage.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument! Use `!help {ctx.command}` to see correct usage.")
    else:
        print(f"Unhandled error in {ctx.command}: {error}")


# =============== START FLASK + BOT TOGETHER ===============

if __name__ == "__main__":
    # Flask runs in background thread, bot runs on main thread (required for asyncio)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Flask thread started")
    
    # Small delay to let Flask bind to port before bot starts
    # (Render health-checks the port immediately on deploy)
    import time
    time.sleep(2)
    
    print("🤖 Starting Discord bot...")
    bot.run(TOKEN, log_handler=None)
