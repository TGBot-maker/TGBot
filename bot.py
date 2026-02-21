import os
import threading
import asyncio
from datetime import datetime, timedelta
import json

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
    app.run(host="0.0.0.0", port=port)


# =============== DISCORD BOT SETUP ===============

load_dotenv()
TOKEN = os.getenv("TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =============== EVENT STORAGE ===============

EVENTS_FILE = "events.json"

def load_events():
    """Load events from JSON file"""
    if os.path.exists(EVENTS_FILE):
        with open(EVENTS_FILE, "r") as f:
            return json.load(f)
    return []

def save_events(events):
    """Save events to JSON file"""
    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=2)

events = load_events()

# =============== INTRUDER ALERT SYSTEM ===============

intruder_alert_enabled = False
intruder_count = 0

# =============== IMAGE POLL STORAGE ===============

active_polls = {}



# =============== EVENT CHECKER TASK ===============

@tasks.loop(minutes=1)
async def check_events():
    """Check every minute if any events need to be triggered"""
    global events
    now = datetime.now()
    
    for event in events[:]:  # Copy list to avoid modification issues
        event_time = datetime.fromisoformat(event["time"])
        reminder_time = event_time - timedelta(minutes=10)
        
        # Check if it's time for the 10-minute reminder
        if not event.get("reminder_sent", False) and now >= reminder_time and now < event_time:
            channel = bot.get_channel(event["channel_id"])
            if channel:
                mention = event["mention"]
                await channel.send(f"⏰ **Reminder:** {mention} - Event '{event['name']}' starts in 10 minutes!")
                event["reminder_sent"] = True
                save_events(events)
        
        # Check if it's time for the actual event
        if now >= event_time and not event.get("event_triggered", False):
            channel = bot.get_channel(event["channel_id"])
            if channel:
                mention = event["mention"]
                await channel.send(f"🔔 **EVENT NOW:** {mention} - '{event['name']}' is starting!")
                event["event_triggered"] = True
                
                # Handle repeating events
                if event.get("repeat_days"):
                    # Schedule next occurrence
                    next_time = event_time + timedelta(days=event["repeat_days"])
                    new_event = event.copy()
                    new_event["time"] = next_time.isoformat()
                    new_event["reminder_sent"] = False
                    new_event["event_triggered"] = False
                    events.append(new_event)
                    await channel.send(f"📅 Next occurrence scheduled for: {next_time.strftime('%Y-%m-%d %H:%M')}")
                
                save_events(events)
        
        # Clean up old one-time events (24 hours after they've triggered)
        if event.get("event_triggered", False) and not event.get("repeat_days"):
            if now > event_time + timedelta(hours=24):
                events.remove(event)
                save_events(events)


@check_events.before_loop
async def before_check_events():
    await bot.wait_until_ready()


# =============== ORIGINAL WELCOME FEATURE ===============

@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")
    check_events.start()  # Start the event checker


@bot.event
async def on_member_join(member):
    template = Image.open("image.png").convert("RGBA")
    avatar_url = member.display_avatar.url
    avatar_bytes = requests.get(avatar_url).content
    avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
    avatar = avatar.resize((140, 140))
    template.paste(avatar, (390, 28), avatar)
    draw = ImageDraw.Draw(template)
    font = ImageFont.truetype("pokemon-gb.ttf", 38)
    username = member.name
    draw.text((190, 469), username, font=font, fill=(0, 0, 0))
    output_path = "welcome.png"
    template.save(output_path)
    channel = member.guild.system_channel
    if channel:
        await channel.send(file=discord.File(output_path))


# =============== EVENT COMMANDS ===============

@bot.command(name="addevent")
async def add_event(ctx, event_name: str, date: str, time: str, mention: str, repeat_days: int = 0):
    """
    Add a new event with optional repeating
    
    Usage: !addevent "Event Name" YYYY-MM-DD HH:MM @role 2
    
    Examples:
    !addevent "Raid Night" 2026-01-29 20:00 @everyone 0
    !addevent "Daily Standup" 2026-01-29 09:00 @team 1
    !addevent "Weekly Meeting" 2026-01-30 15:00 @staff 7
    """
    try:
        # Parse the datetime
        event_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        
        # Check if the event is in the future
        if event_datetime <= datetime.now():
            await ctx.send("❌ Event time must be in the future!")
            return
        
        # Create event object
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
            f"✅ Event added successfully!\n"
            f"**Event:** {event_name}\n"
            f"**Time:** {event_datetime.strftime('%Y-%m-%d %H:%M')}\n"
            f"**Mention:** {mention}\n"
            f"**Reminder:** 10 minutes before{repeat_info}"
        )
        
    except ValueError:
        await ctx.send("❌ Invalid date/time format! Use: YYYY-MM-DD HH:MM (24-hour format)")
    except Exception as e:
        await ctx.send(f"❌ Error creating event: {str(e)}")


@bot.command(name="listevents")
async def list_events(ctx):
    """List all upcoming events"""
    if not events:
        await ctx.send("📅 No events scheduled.")
        return
    
    now = datetime.now()
    upcoming = [e for e in events if datetime.fromisoformat(e["time"]) > now]
    
    if not upcoming:
        await ctx.send("📅 No upcoming events.")
        return
    
    # Sort by time
    upcoming.sort(key=lambda x: x["time"])
    
    embed = discord.Embed(title="📅 Upcoming Events", color=discord.Color.blue())
    
    for i, event in enumerate(upcoming[:10], 1):  # Show max 10 events
        event_time = datetime.fromisoformat(event["time"])
        time_until = event_time - now
        
        repeat_info = f"\n🔁 Repeats every {event['repeat_days']} days" if event.get("repeat_days") else ""
        
        embed.add_field(
            name=f"{i}. {event['name']}",
            value=f"⏰ {event_time.strftime('%Y-%m-%d %H:%M')}\n"
                  f"👥 {event['mention']}\n"
                  f"⏳ In {time_until.days}d {time_until.seconds//3600}h {(time_until.seconds//60)%60}m"
                  f"{repeat_info}",
            inline=False
        )
    
    await ctx.send(embed=embed)


@bot.command(name="deleteevent")
async def delete_event(ctx, event_index: int):
    """
    Delete an event by its index from the list
    
    Usage: !deleteevent 1
    (Use !listevents to see event numbers)
    """
    now = datetime.now()
    upcoming = [e for e in events if datetime.fromisoformat(e["time"]) > now]
    upcoming.sort(key=lambda x: x["time"])
    
    if event_index < 1 or event_index > len(upcoming):
        await ctx.send(f"❌ Invalid event number! Use !listevents to see available events.")
        return
    
    event_to_delete = upcoming[event_index - 1]
    events.remove(event_to_delete)
    save_events(events)
    
    await ctx.send(f"✅ Deleted event: '{event_to_delete['name']}'")


@bot.command(name="eventhelp")
async def event_help(ctx):
    """Show help for event commands"""
    embed = discord.Embed(
        title="🎯 Event System Help",
        description="Manage events and reminders with these commands:",
        color=discord.Color.green()
    )
    
    embed.add_field(
        name="!addevent",
        value='**Create a new event**\n'
              'Usage: `!addevent "Name" YYYY-MM-DD HH:MM @mention [repeat_days]`\n'
              'Examples:\n'
              '• `!addevent "Raid" 2026-01-29 20:00 @everyone 0`\n'
              '• `!addevent "Daily" 2026-01-29 09:00 @team 1` (repeats daily)\n'
              '• `!addevent "Weekly" 2026-01-30 15:00 @staff 7` (repeats weekly)',
        inline=False
    )
    
    embed.add_field(
        name="!listevents",
        value="**View all upcoming events**\nShows next 10 events with countdown timers",
        inline=False
    )
    
    embed.add_field(
        name="!deleteevent",
        value="**Delete an event**\nUsage: `!deleteevent 1`\n(Use !listevents to see event numbers)",
        inline=False
    )
    
    embed.add_field(
        name="⏰ Reminders",
        value="• You'll get a reminder **10 minutes before** each event\n"
              "• The event notification is sent at the scheduled time\n"
              "• Repeating events automatically schedule the next occurrence",
        inline=False
    )
    
    await ctx.send(embed=embed)


# =============== INTRUDER ALERT COMMANDS ===============

@bot.command(name="stop intruding")
async def stop_intruding(ctx):
    """Toggle the intruder alert system"""
    global intruder_alert_enabled, intruder_count
    
    intruder_alert_enabled = not intruder_alert_enabled
    intruder_count = 0
    
    if intruder_alert_enabled:
        embed = discord.Embed(
            title="🚨 INTRUDER ALERT SYSTEM ACTIVATED 🚨",
            description="Monitoring for intruders...",
            color=discord.Color.red()
        )
        embed.add_field(name="Status", value="🔴 LIVE", inline=False)
        embed.add_field(name="Action", value="Any messages from the 'Intruder' role will be immediately BANISHED to the shadow realm", inline=False)
        embed.set_footer(text="The intruder hunters are now watching...")
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="✅ INTRUDER ALERT SYSTEM DEACTIVATED ✅",
            description="Intruders may roam freely once more...",
            color=discord.Color.green()
        )
        embed.add_field(name="Status", value="🟢 OFFLINE", inline=False)
        embed.add_field(name="Intruders Caught", value=f"⚡ {intruder_count}", inline=False)
        await ctx.send(embed=embed)

    
@bot.event
async def on_message(message):
    """Check for intruders and delete their messages"""
    global intruder_alert_enabled, intruder_count
    
    # Don't process bot messages
    if message.author.bot:
        await bot.process_commands(message)
        return
    
    # Check if intruder alert is enabled
    if intruder_alert_enabled:
        # Check if user has the "Intruder" role
        intruder_role = discord.utils.get(message.guild.roles, name="Intruder")
        
        if intruder_role and intruder_role in message.author.roles:
            # Delete the intruder message
            await message.delete()
            intruder_count += 1
            
            # Create a funny alert message
            alerts = [
                f"🚨🚨🚨 **RED ALERT! RED ALERT!** 🚨🚨🚨\n\n"
                f"An INTRUDER has been detected!\n"
                f"Message from **{message.author.mention}** deleted and sent to the SHADOW REALM™\n"
                f"Total intruders caught: **{intruder_count}**\n"
                f"🔴 STATUS: CODE RED 🔴",
                
                f"📛 **INTRUDER DETECTED** 📛\n\n"
                f"*WOOP WOOP WOOP WOOP* 🚨\n"
                f"Message from {message.author.mention} has been VAPORIZED!\n"
                f"Intruders neutralized: {intruder_count}\n"
                f"*The council is pleased* ✨",
                
                f"🛑 **HALT!** 🛑\n\n"
                f"An uninvited guest tried to speak!\n"
                f"{message.author.mention}'s message was yeeted into oblivion\n"
                f"Intruders caught so far: **{intruder_count}**\n"
                f"*Mission: PROTECTING THE SERVER* ✓",
                
                f"🔴 **INTRUDER PROTOCOL INITIATED** 🔴\n\n"
                f"BEEP BEEP BEEP 🚨\n"
                f"Incoming message from {message.author.mention}...\n"
                f"⚙️ Scanning... ⚙️\n"
                f"🎯 **IDENTIFIED AS INTRUDER**\n"
                f"Message deleted successfully\n"
                f"Total bounties collected: {intruder_count}",
                
                f"😤 **AN INTRUDER IN MY PRESENCE?!** 😤\n\n"
                f"{message.author.mention} tried to pull a fast one...\n"
                f"NOPE! Message OBLITERATED immediately!\n"
                f"You cannot hide from the watchers. We have caught: {intruder_count} intruders\n"
                f"*All is well in the kingdom* 👑"
            ]
            
            import random
            alert_message = random.choice(alerts)
            
            await message.channel.send(alert_message)
    
    # Process commands even if intruder
    await bot.process_commands(message)

    
@bot.command(name="imagepoll")
async def image_poll(
    ctx,
    title: str,
    img1: str,
    img2: str,
    img3: str = None,
    img4: str = None,
    duration: int = None
):
    """
    Create a banner-style image poll.

    Example:
    !imagepoll "Best Banner"
    https://img1.png
    https://img2.png
    https://img3.png
    5
    """

    image_urls = [img1, img2]

    if img3:
        image_urls.append(img3)

    if img4:
        image_urls.append(img4)

    embeds = []

    for i, url in enumerate(image_urls):
        emb = discord.Embed(description=f"**Option {i+1}**")
        emb.set_image(url=url)
        emb.set_footer(text="Votes: 0")
        embeds.append(emb)

    view = ImagePollView(title, image_urls)

    msg = await ctx.send(
        content=f"**{title}**",
        embeds=embeds,
        view=view
    )

    view.message = msg
    active_polls[msg.id] = view

    if duration:
        bot.loop.create_task(close_image_poll(msg.id, duration))



# =============== IMAGE POLL SYSTEM ===============

class ImagePollView(discord.ui.View):
    def __init__(self, title, options):
        super().__init__(timeout=None)

        self.title = title
        self.options = [
            {"label": label, "img": img, "votes": 0}
            for label, img in options
        ]

        self.voters = {}   # user_id -> option index
        self.message = None
        self.closed = False

        for idx, opt in enumerate(options):
            self.add_item(ImagePollButton(idx, opt[0]))


# =============== IMAGE POLL SYSTEM ===============

class ImagePollView(discord.ui.View):
    def __init__(self, title, image_urls):
        super().__init__(timeout=None)

        self.title = title
        self.options = [
            {"img": url, "votes": 0}
            for url in image_urls
        ]

        self.voters = {}   # user_id -> option index
        self.message = None
        self.closed = False

        for idx in range(len(image_urls)):
            self.add_item(ImagePollButton(idx))


class ImagePollButton(discord.ui.Button):
    def __init__(self, index):
        super().__init__(
            label=f"{index+1}️⃣",
            style=discord.ButtonStyle.primary,
            custom_id=f"imgpoll_{index}"
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):

        view: ImagePollView = self.view

        if view.closed:
            return await interaction.response.send_message(
                "❌ This poll is already closed.",
                ephemeral=True
            )

        uid = interaction.user.id
        prev_vote = view.voters.get(uid)

        if prev_vote == self.index:
            view.voters.pop(uid)
            view.options[self.index]["votes"] -= 1
            msg = f"Removed vote for **Option {self.index+1}**."
        else:
            if prev_vote is not None:
                view.options[prev_vote]["votes"] -= 1

            view.voters[uid] = self.index
            view.options[self.index]["votes"] += 1
            msg = f"Voted for **Option {self.index+1}**."

        await update_image_poll(view)
        await interaction.response.send_message(msg, ephemeral=True)


async def update_image_poll(view: ImagePollView):

    embeds = []

    for i, opt in enumerate(view.options):
        emb = discord.Embed(description=f"**Option {i+1}**")
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
    winners = [
        f"Option {i+1}"
        for i, o in enumerate(view.options)
        if o["votes"] == max_votes
    ]

    result = "Winner: " + ", ".join(winners) if max_votes else "No votes."

    await view.message.edit(
        content=f"**{view.title}** — Poll closed. {result}",
        view=view
    )


    await asyncio.sleep(minutes * 60)

    view = active_polls.get(message_id)
    if not view:
        return

    view.closed = True

    for item in view.children:
        item.disabled = True

    max_votes = max(o["votes"] for o in view.options)
    winners = [o["label"] for o in view.options if o["votes"] == max_votes]

    result = "Winner: " + ", ".join(winners) if max_votes else "No votes."

    await view.message.edit(
        content=f"**{view.title}** — Poll closed. {result}",
        view=view
    )


# =============== START FLASK + BOT TOGETHER ===============

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)