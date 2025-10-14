import discord
from discord import app_commands
from discord.ext import commands
import asyncio, random, csv, os
from datetime import datetime, timezone
from dotenv import load_dotenv
from typing import Dict, List, Optional

# Load token
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_TOKEN in .env")

# Bot setup
INTENTS = discord.Intents.default()
INTENTS.message_content = True
bot = commands.Bot(command_prefix="!", intents=INTENTS)

# Config constants
BALANCE_FILE = "balances.csv"
DAILY_FILE = "daily.csv"
HORSE_HISTORY_FILE = "horse_history.csv"

START_BALANCE = 10000
MIN_BET = 200

RACE_DISTANCE = 80       # maximum distance
BAR_LENGTH = 20          # visual blocks in progress bar
TICK_SECONDS = 1         # update every 1 second
STAT_UPDATE_SECONDS = 3  # update stats every 3 seconds
STAT_MIN = 200
STAT_MAX = 1200
STAT_DELTA_MIN = 50
STAT_DELTA_MAX = 100

PAYOUTS = {1: 2.7, 2: 2.0, 3: 1.5}

# Horse pool with your provided emoji IDs (updated)
HORSE_POOL = [
    {"emoji": "<:gold_ship:1427257561802997841>", "name": "Gold Ship"},
    {"emoji": "<:haru_urara:1427257575996391497>", "name": "Haru Urara"},
    {"emoji": "<:kitasan_black:1427257395247185960>", "name": "Kitasan Black"},
    {"emoji": "<:oguri_cap:1427257240062132374>", "name": "Oguri Cap"},
    {"emoji": "<:satono_diamond:1427257543603781672>", "name": "Satono Diamond"},
    {"emoji": "<:special_week:1427257295812952164>", "name": "Special Week"},
    {"emoji": "<:tamamo_cross:1427257518538887240>", "name": "Tamamo Cross"},
    {"emoji": "<:tokai_teio:1427257361059414037>", "name": "Tokai Teio"},
]

# Special Horses Pool
SPECIAL_HORSES_POOL = [
    {"emoji": "<:vedalNom:1427276755194085457>", "name": "Vedal", "special_type": "vedal"},
    {"emoji": "<:sonic:1427276770184527873>", "name": "Sonic", "special_type": "sonic"},
]

# File helpers
def load_csv_map(filepath: str) -> Dict[str, str]:
    if not os.path.exists(filepath):
        return {}
    out = {}
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                out[row[0]] = row[1]
    return out

def save_csv_map(filepath: str, d: Dict[str, str]):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for k, v in d.items():
            writer.writerow([k, v])

# Balances
def load_balances() -> Dict[str, int]:
    raw = load_csv_map(BALANCE_FILE)
    out = {}
    for k, v in raw.items():
        try:
            out[k] = int(v)
        except ValueError: # Handle potential error if value is not an int
            out[k] = START_BALANCE
    return out

def save_balances(bal: Dict[str, int]):
    raw = {k: str(v) for k, v in bal.items()}
    save_csv_map(BALANCE_FILE, raw)

def get_balance(uid: str) -> int:
    b = load_balances()
    if uid not in b:
        b[uid] = START_BALANCE
        save_balances(b) # Save immediately if new user
    return b[uid]

def change_balance(uid: str, delta: int) -> int:
    b = load_balances()
    cur = b.get(uid, START_BALANCE)
    cur += delta
    if cur < 0:
        cur = 0 # Ensure balance does not go below zero
    b[uid] = cur
    save_balances(b)
    return cur

# Daily rewards
def load_daily() -> Dict[str, str]:
    return load_csv_map(DAILY_FILE)

def save_daily(d: Dict[str, str]):
    save_csv_map(DAILY_FILE, d)

# Horse history
def load_history() -> Dict[str, int]:
    raw = load_csv_map(HORSE_HISTORY_FILE)
    out = {}
    for k, v in raw.items():
        try:
            out[k] = int(v)
        except ValueError: # Handle potential error if value is not an int
            out[k] = 0
    return out

def save_history(hist: Dict[str, int]):
    raw = {k: str(v) for k, v in hist.items()}
    save_csv_map(HORSE_HISTORY_FILE, raw)

# Stats and movement helpers
def init_stats() -> Dict[str, int]:
    return {
        "speed": random.randint(STAT_MIN, STAT_MAX),
        "power": random.randint(STAT_MIN, STAT_MAX),
        "stamina": random.randint(STAT_MIN, STAT_MAX),
        "agility": random.randint(STAT_MIN, STAT_MAX),
        "focus": random.randint(STAT_MIN, STAT_MAX)
    }

def get_special_horse_stats(special_type: str) -> Dict[str, int]:
    if special_type == "vedal":
        return {
            "speed": 1,
            "power": 1,
            "stamina": 1,
            "agility": 1,
            "focus": 1
        }
    elif special_type == "sonic":
        return {
            "speed": 9999,
            "power": 9999,
            "stamina": 9999,
            "agility": 9999,
            "focus": 9999
        }
    return init_stats() # Fallback, though should not be reached for special horses

def clamp_stat(v:int) -> int:
    return max(STAT_MIN, min(STAT_MAX, v))

def stats_to_move_bonus(stats: Dict[str,int], horse_name: str) -> float:
    # If Vedal, always move 1 step (base move 1 + bonus 0)
    if horse_name == "Vedal":
        return 0
    # If Sonic, always move 79 steps (base move 1 + bonus 78)
    if horse_name == "Sonic":
        return 78

    # Old logic for normal horses
    s = stats["speed"]*0.35 + stats["power"]*0.3 + stats["stamina"]*0.2 + stats["agility"]*0.1 + stats["focus"]*0.05
    s_min = STAT_MIN * 1.0
    s_max = STAT_MAX * 1.0
    frac = (s - s_min) / (s_max - s_min) if s_max != s_min else 0.0
    return frac * 4.0

def render_bar(pos:int) -> tuple[str, int]:
    frac = max(0.0, min(pos / RACE_DISTANCE, 1.0))
    filled = int(round(frac * BAR_LENGTH))
    bar = "█" * filled + "░" * (BAR_LENGTH - filled)
    return bar, int(round(frac*100))

def mood_emoji_from_delta(delta:int) -> str:
    if delta >= 200:
        return "💨"
    if delta >= 100:
        return "💪"
    if delta <= -200:
        return "😫"
    if delta <= -100:
        return "😮‍💨"
    return "⚡" # Default neutral/average mood

# Small chance of fall when big move
def check_fall(move:int) -> bool:
    # if move large (>8) small chance else tiny chance
    if move >= 10:
        return random.random() < 0.06  # 6%
    if move >= 8:
        return random.random() < 0.03  # 3%
    return random.random() < 0.005     # 0.5%

# Race locks per channel to avoid overlapping races
race_locks: Dict[int, bool] = {}

# Bot ready sync
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Error syncing slash commands: {e}")
    print(f"✅ Logged in as {bot.user} — slash commands synced.")

# /umarace command
@bot.tree.command(name="umarace", description="Tham gia đua ngựa và đặt cược xu (>=200).")
@app_commands.describe(bet="Số xu bạn muốn cược (>=200)")
async def umarace(interaction: discord.Interaction, bet: int):
    uid = str(interaction.user.id)
    bal = get_balance(uid)
    if bet < MIN_BET:
        await interaction.response.send_message(f"⚠️ Mức cược tối thiểu là {MIN_BET} xu.", ephemeral=True)
        return
    if bet > bal:
        await interaction.response.send_message(f"❌ Bạn không đủ xu để cược! (Hiện có: {bal} xu)", ephemeral=True)
        return

    cid = interaction.channel_id
    if race_locks.get(cid):
        await interaction.response.send_message("❌ Đã có cuộc đua đang diễn ra trong kênh này. Vui lòng chờ.", ephemeral=True)
        return

    # Special horse logic
    special_message = ""
    race_participants = HORSE_POOL.copy() # Start with all normal horses

    roll = random.random() # 0.0 to 1.0

    if roll < 0.05: # 5% chance for both special horses
        # Add both Vedal and Sonic
        race_participants.extend(SPECIAL_HORSES_POOL) # Add both
        special_message = "💥 **Sự kiện đặc biệt: Vedal và Sonic đã tham gia cuộc đua!** Chúc may mắn!"
    elif roll < 0.15: # 10% chance (0.05 to 0.15) for one of the two (total 15% for event)
        chosen_special = random.choice(SPECIAL_HORSES_POOL)
        race_participants.append(chosen_special)
        special_message = f"🌟 **Sự kiện đặc biệt: {chosen_special['name']} đã xuất hiện!** Cuộc đua thêm phần kịch tính!"

    # Ensure options for user selection are only normal horses
    # Users cannot bet on special horses
    options = [discord.SelectOption(label=h["name"], value=h["name"], emoji=h["emoji"]) for h in HORSE_POOL]
    # End Special Horse Logic

    class HorseSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="Chọn ngựa để cược...", min_values=1, max_values=1, options=options)

        async def callback(self, select_inter: discord.Interaction):
            chosen_name = self.values[0]
            chosen = next(x for x in HORSE_POOL if x["name"] == chosen_name) # Ensure user chose a normal horse

            # Prepare initial race content here before editing the message

            # Subtract bet immediately
            new_bal_after_bet = change_balance(uid, -bet)

            # Initialize stats for ALL horses participating in THIS race
            initial_stats_for_race = {}
            for p in race_participants: # Use race_participants which may include special horses
                if "special_type" in p: # Check if it's a special horse
                    initial_stats_for_race[p["name"]] = get_special_horse_stats(p["special_type"])
                else:
                    initial_stats_for_race[p["name"]] = init_stats()

            # Get stats for the chosen horse to display
            s_ch = initial_stats_for_race[chosen["name"]]
            odds_display = round(random.uniform(2.5, 4.0), 1)

            header_lines = []
            header_lines.append("🏇 CUỘC ĐUA SẮP BẮT ĐẦU!")
            header_lines.append("")
            if special_message: # Add special event message if present
                header_lines.append(f"{special_message}\n")
            header_lines.append(f"Bạn chọn: {chosen['emoji']} **{chosen['name']}**")
            header_lines.append(f"Tỷ lệ cược (tham khảo hiển thị): **x{odds_display}**")
            header_lines.append(f"Chỉ số: 🏃‍♀️{s_ch['speed']} 💪{s_ch['power']} ⚡{s_ch['stamina']} 💃{s_ch['agility']} 💡{s_ch['focus']}")
            header_lines.append(f"Cược: **{bet} xu** → Có thể thắng (tham khảo): **{int(round(bet*odds_display))} xu**")
            header_lines.append("")
            header_lines.append("━━━━━━━━━━━━━━━━━━━━🏁")
            header_lines.append("Đang khởi động...")
            header_lines.append("")
            header_lines.append(f"Số dư sau khi cược: {new_bal_after_bet} xu — {datetime.now(timezone.utc).astimezone().strftime('%c')}")
            initial_race_content = "\n".join(header_lines)

            await select_inter.response.edit_message(content=initial_race_content, view=None)
            msg = await select_inter.original_response()

            race_locks[cid] = True
            try:
                await run_race(select_inter, uid, bet, chosen, race_participants, msg, new_bal_after_bet, initial_stats_for_race, odds_display, special_message)
            finally:
                race_locks[cid] = False

    view = discord.ui.View(timeout=30)
    view.add_item(HorseSelect())
    await interaction.response.send_message("🎲 Vui lòng chọn ngựa để đặt cược (dropdown):", view=view)

# Race engine
async def run_race(interaction: discord.Interaction, uid: str, bet: int, chosen: dict, participants: List[dict],
                   msg: discord.Message, initial_balance_after_bet: int,
                   initial_stats: Dict[str, Dict[str, int]], odds_display: float, special_event_message: str):

    new_bal_after_bet = initial_balance_after_bet
    stats = initial_stats

    positions = {p["name"]: 0 for p in participants}
    last_stat_delta = {p["name"]: 0 for p in participants}
    finished: List[str] = []
    final_bars: Dict[str, tuple] = {}
    ticks = 0

    # Main race loop
    while True:
        await asyncio.sleep(TICK_SECONDS)
        ticks += 1

        # Movement & possible falls / special states
        for p in participants:
            name = p["name"]
            if name in finished:
                continue

            # Special movement for Vedal and Sonic
            if name == "Vedal":
                move = 1 # Vedal always moves 1 step
                fell = False # Vedal never falls
            elif name == "Sonic":
                move = 79 # Sonic always moves 79 steps
                fell = False # Sonic never falls
            else:
                # Base random move 1..6 for normal horses
                base = random.randint(1,6)
                bonus = stats_to_move_bonus(stats[name], name) # Pass horse name to handle Vedal/Sonic
                move = base + int(round(bonus))

                # Check fall chance (small)
                fell = False
                if check_fall(move):
                    loss_pct = random.randint(5,10)
                    loss = max(1, int(RACE_DISTANCE * loss_pct / 100))
                    positions[name] = max(0, positions[name] - loss)
                    fell = True

                # Apply fatigue: if stamina low below threshold may skip tick (simulate exhaustion)
                if stats[name]["stamina"] < (STAT_MIN + (STAT_MAX-STAT_MIN)*0.25):
                    if random.random() < 0.08:
                        last_stat_delta[name] = -random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)
                        continue # Skip move this tick

            # Apply move
            positions[name] += move

            # Cap position at RACE_DISTANCE
            if positions[name] >= RACE_DISTANCE:
                positions[name] = RACE_DISTANCE
                if name not in finished:
                    finished.append(name)
                    bar, pct = render_bar(positions[name])
                    final_bars[name] = (bar, pct)

            if fell:
                last_stat_delta[name] = -random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)

        # Every STAT_UPDATE_SECONDS update stats (but not for special horses)
        if ticks % STAT_UPDATE_SECONDS == 0:
            for p in participants:
                name = p["name"]
                # Only update stats for normal horses
                if "special_type" not in p:
                    key = random.choice(list(stats[name].keys()))
                    delta = random.choice([-1,1]) * random.randint(STAT_DELTA_MIN, STAT_DELTA_MAX)
                    old = stats[name][key]
                    stats[name][key] = clamp_stat(old + delta)
                    last_stat_delta[name] = stats[name][key] - old
                else:
                    # Special horses do not change stats, set delta to 0 so mood emoji is unaffected
                    last_stat_delta[name] = 0


        # Build race display lines (bars fixed length)
        lines = []
        for p in participants:
            name = p["name"]
            pos = positions[name]
            if name in final_bars:
                bar, pct = final_bars[name]
            else:
                bar, pct = render_bar(pos)

            # Get mood emoji, but if it's a special horse, always use ✨
            mood = mood_emoji_from_delta(last_stat_delta.get(name, 0))
            if "special_type" in p: # If it's a special horse
                mood = "✨"

            s = stats[name]
            # A single-line display per your requested format: emoji bar (pos) mood
            # and then stats on next inline code line to keep message compact
            lines.append(f"{p['emoji']} {bar} ({min(pos, RACE_DISTANCE)}) {mood}")
            lines.append(f"🏃‍♀️{s['speed']} 💪{s['power']} ⚡{s['stamina']} 💃{s['agility']} 💡{s['focus']}")

        # Construct full message content (single message)
        header_lines = []
        header_lines.append("🏇 CUỘC ĐUA ĐANG DIỄN RA!")
        header_lines.append("")
        if special_event_message: # Add special event message if present
            header_lines.append(f"{special_event_message}\n")
        header_lines.append(f"Bạn chọn: {chosen['emoji']} **{chosen['name']}**")
        header_lines.append(f"Tỷ lệ cược (tham khảo): **x{odds_display}**")
        s_ch = stats[chosen['name']]
        header_lines.append(f"Chỉ số (hiện tại): 🏃‍♀️{s_ch['speed']} 💪{s_ch['power']} ⚡{s_ch['stamina']} 💃{s_ch['agility']} 💡{s_ch['focus']}")
        header_lines.append(f"Cược: **{bet} xu** → Có thể thắng (tham khảo): **{int(round(bet*odds_display))} xu**")
        header_lines.append("")
        header_lines.append("━━━━━━━━━━━━━━━━━━━━🏁")
        header_lines.extend(lines)
        header_lines.append("")
        header_lines.append(f"Số dư sau khi cược: {new_bal_after_bet} xu")
        header_lines.append(f"Tick: {ticks} — {datetime.now(timezone.utc).astimezone().strftime('%c')}")

        content = "\n".join(header_lines)

        try:
            await msg.edit(content=content)
        except discord.errors.NotFound:
            print(f"Failed to edit race message in channel {interaction.channel_id}: Message not found.")
            break
        except Exception as e:
            print(f"Error editing race message: {e}")
            pass

        if len(finished) >= 3:
            break

    # Finalize results: top3, update history and payouts
    top3 = finished[:3]
    history = load_history()
    # Only update history for normal horses
    if top3:
        for horse_name in top3:
            # Check if the horse is not special before updating history
            is_special = any(sh['name'] == horse_name for sh in SPECIAL_HORSES_POOL)
            if not is_special:
                history[horse_name] = history.get(horse_name, 0) + 1
        save_history(history)

    # Compute payout based on place if chosen in top3
    place = None
    if chosen["name"] in top3:
        place = top3.index(chosen["name"]) + 1

    if place == 1:
        reward = int(round(bet * PAYOUTS[1]))
        new_bal_final = change_balance(uid, reward)
        outcome_line = f"✅ **THẮNG 1st!** +{reward} xu"
    elif place == 2:
        reward = int(round(bet * PAYOUTS[2]))
        new_bal_final = change_balance(uid, reward)
        outcome_line = f"✅ **THẮNG 2nd!** +{reward} xu"
    elif place == 3:
        reward = int(round(bet * PAYOUTS[3]))
        new_bal_final = change_balance(uid, reward)
        outcome_line = f"✅ **THẮNG 3rd!** +{reward} xu"
    else:
        new_bal_final = get_balance(uid) # Balance already subtracted the bet
        outcome_line = f"❌ **THUA!** -{bet} xu"

    # Build final content (single message, do not delete bars)
    final_lines = []
    final_lines.append("🏇 CUỘC ĐUA KẾT THÚC!")
    final_lines.append("")
    if special_event_message: # Add special event message if present
        final_lines.append(f"{special_event_message}\n")
    final_lines.append(f"Bạn chọn: {chosen['emoji']} **{chosen['name']}**")
    final_lines.append(f"Tỷ lệ cược (tham khảo): **x{odds_display}**")
    s_ch = stats[chosen['name']]
    final_lines.append(f"Chỉ số: 🏃‍♀️{s_ch['speed']} 💪{s_ch['power']} ⚡{s_ch['stamina']} 💃{s_ch['agility']} 💡{s_ch['focus']}")
    final_lines.append(f"Cược: **{bet} xu** → Có thể thắng: **{int(round(bet*odds_display))} xu**")
    final_lines.append("")
    final_lines.append("━━━━━━━━━━━━━━━━━━━━🏁")
    # Final bars
    for p in participants:
        name = p["name"]
        if name in final_bars:
            bar, pct = final_bars[name]
        else:
            bar, pct = render_bar(positions[name])
        s = stats[name]

        mood = mood_emoji_from_delta(last_stat_delta.get(name,0))
        if "special_type" in p: # If it's a special horse
            mood = "✨"

        final_lines.append(f"{p['emoji']} {bar} ({min(positions[name], RACE_DISTANCE)}) {mood}")
        final_lines.append(f"🏃‍♀️{s['speed']} 💪{s['power']} ⚡{s['stamina']} 💃{s['agility']} 💡{s['focus']}")
    final_lines.append("")
    # Results
    final_lines.append("**━━━━━ KẾT QUẢ ━━━━━**")
    medals = ["🥇", "🥈", "🥉"]
    for i in range(3):
        if i < len(top3):
            horse_name = top3[i]
            horse = next(h for h in participants if h["name"] == horse_name)
            final_lines.append(f"{medals[i]} **{i+1}st:** {horse['emoji']} {horse['name']}")
        else:
            final_lines.append(f"{medals[i]} —")
    final_lines.append("")
    final_lines.append(outcome_line)
    final_lines.append(f"💰 Tổng xu: **{new_bal_final} xu**")

    final_content = "\n".join(final_lines)
    try:
        await msg.edit(content=final_content)
    except discord.errors.NotFound:
        print(f"Failed to edit final race message in channel {interaction.channel_id}: Message not found.")
    except Exception as e:
        print(f"Error editing final race message: {e}")
        pass

# /balance command
@bot.tree.command(name="balance", description="Xem số xu hiện tại của bạn hoặc người khác")
@app_commands.describe(user="Người muốn xem (tùy chọn)")
async def balance_cmd(interaction: discord.Interaction, user: Optional[discord.User] = None):
    target = user or interaction.user
    bal = get_balance(str(target.id))
    await interaction.response.send_message(f"💰 {target.display_name} hiện có {bal} xu")

# /top command
@bot.tree.command(name="top", description="Xem top 10 người giàu nhất")
async def top_cmd(interaction: discord.Interaction):
    b = load_balances()
    if not b:
        await interaction.response.send_message("Chưa có người chơi nào.")
        return
    items = sorted(b.items(), key=lambda kv: kv[1], reverse=True)[:10]

    embed = discord.Embed(
        title="🏆 Top 10 Người Giàu Nhất 🏆",
        description="Những tay chơi cừ khôi nhất!",
        color=discord.Color.gold()
    )

    for i, (uid, coins) in enumerate(items, start=1):
        user_obj = bot.get_user(int(uid)) # Try to get user object
        username = user_obj.display_name if user_obj else f"Người dùng không rõ ({uid})"
        embed.add_field(name=f"{i}. {username}", value=f"**{coins} xu**", inline=False)

    await interaction.response.send_message(embed=embed)

# /daily command
@bot.tree.command(name="daily", description="Nhận thưởng hàng ngày (1-2000 xu, 1 lần/ngày)")
async def daily_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    daily = load_daily()
    today = datetime.now(timezone.utc).date().isoformat()
    last = daily.get(uid)
    if last == today:
        await interaction.response.send_message("❌ Bạn đã nhận thưởng hôm nay rồi.")
        return
    reward = random.randint(1,2000)
    new = change_balance(uid, reward)
    daily[uid] = today
    save_daily(daily)
    await interaction.response.send_message(f"🎁 Bạn nhận được {reward} xu! Tổng: {new} xu")

# /give command
@bot.tree.command(name="give", description="Chuyển xu cho người khác")
@app_commands.describe(user="Người nhận", amount="Số xu muốn chuyển")
async def give_cmd(interaction: discord.Interaction, user: discord.User, amount: int):
    sender = str(interaction.user.id)
    receiver = str(user.id)
    if amount <= 0:
        await interaction.response.send_message("❌ Số tiền không hợp lệ.", ephemeral=True)
        return
    if sender == receiver:
        await interaction.response.send_message("❌ Bạn không thể tự chuyển xu cho mình.", ephemeral=True)
        return
    if get_balance(sender) < amount:
        await interaction.response.send_message("❌ Bạn không đủ xu để chuyển.", ephemeral=True)
        return
    change_balance(sender, -amount)
    change_balance(receiver, amount)
    await interaction.response.send_message(f"💸 Bạn đã chuyển {amount} xu cho {user.mention}. Tổng bạn còn: {get_balance(sender)} xu")

# Run bot
bot.run(TOKEN)