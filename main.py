import os
import re
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
 
import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands, tasks
 
# ─────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("barrier_bot")
 
TOKEN = os.environ.get("DISCORD_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
KST = ZoneInfo(os.environ.get("TZ", "Asia/Seoul"))
AUTO_CHANNEL_NAME = "🤖｜장벽봇"
PANEL_TITLE = "🛡️ 장벽봇 · 보스 & 콘텐츠 시간표"
SCHEDULE_FILE = "schedule.json"
 
# ─────────────────────────────────────────
# PlayNC 게시판 (페이지가 실제로 쓰는 API 직접 호출)
# ─────────────────────────────────────────
API_BASE = "https://api-community.plaync.com/aion2/board"
BOARDS = {
    "notice": {
        "api_key": "notice_ko",
        "list_url": "https://aion2.plaync.com/ko-kr/board/notice/list",
        "view_url": "https://aion2.plaync.com/ko-kr/board/notice/view?articleId={id}",
        "label": "공지사항",
        "alert_title": "📢 [공지사항] 새 글이 등록되었습니다!",
        "color": discord.Color.blue(),
    },
    "update": {
        "api_key": "update_ko",
        "list_url": "https://aion2.plaync.com/ko-kr/board/update/list",
        "view_url": "https://aion2.plaync.com/ko-kr/board/update/view?articleId={id}",
        "label": "업데이트",
        "alert_title": "🚀 [업데이트] 새 패치노트가 등록되었습니다!",
        "color": discord.Color.green(),
    },
}
 
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://aion2.plaync.com",
    "Referer": "https://aion2.plaync.com/",
}
 
seen_ids = {"notice": None, "update": None}
last_alert_time = ""
 
# ─────────────────────────────────────────
# 일정 데이터 (디스코드 패널에서 추가/수정/삭제 가능)
# ─────────────────────────────────────────
DEFAULT_SCHEDULE = {
    "02:55": "📢 [아그로/카이라] 5분 전 (03:00 시작)",
    "08:55": "📢 [아그로/카이라] 5분 전 (09:00 시작)",
    "18:50": "📢 [어비스균열] 10분 전 (19:00 시작)",
    "19:00": "📝 [오늘의 숙제] 일일 콘텐츠를 챙겨 주세요!",
    "20:55": "📢 [아그로/카이라] 5분 전 (21:00 시작)",
    "21:50": "📢 [어비스균열] 10분 전 (22:00 시작)",
}
schedule: dict[str, str] = {}
schedule_loaded = False
 
# 패널 메시지 위치 기억 (일정이 바뀌면 모든 패널을 자동 갱신)
panel_messages: set[tuple[int, int]] = set()   # (channel_id, message_id)
 
TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{2})\s*$")
PANEL_LINE_RE = re.compile(r"^⏰ `(\d{2}:\d{2})` ➔ (.+)$")
 
 
def normalize_time(text: str):
    """'9:05', '09:05' → '09:05' / 잘못된 값이면 None"""
    m = TIME_RE.match(text)
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2))
    if h > 23 or mnt > 59:
        return None
    return f"{h:02d}:{mnt:02d}"
 
 
def sort_schedule():
    global schedule
    schedule = dict(sorted(schedule.items()))
 
 
def save_schedule():
    try:
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump(schedule, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"일정 파일 저장 실패: {e}")
 
 
def load_schedule_file() -> bool:
    global schedule
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        schedule = {t: m for t, m in data.items() if normalize_time(t)}
        sort_schedule()
        logger.info(f"[일정] 파일에서 {len(schedule)}개 불러옴")
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"일정 파일 읽기 실패: {e}")
        return False
 
 
def parse_schedule_from_embed(embed: discord.Embed) -> dict:
    result = {}
    for line in (embed.description or "").splitlines():
        m = PANEL_LINE_RE.match(line.strip())
        if m:
            result[m.group(1)] = m.group(2)
    return result
 
 
async def restore_schedule():
    """
    봇 시작 시 일정 복원 순서:
    1) schedule.json 파일  2) 알림 채널에 떠 있는 패널 내용  3) 기본값
    (Render 무료 플랜은 재배포 시 파일이 지워지므로 2번이 실제 백업 역할)
    """
    global schedule, schedule_loaded
    if schedule_loaded:
        return
 
    from_file = load_schedule_file()
    from_panel = None
 
    for ch in get_target_channels():
        try:
            async for msg in ch.history(limit=50):
                if (msg.author.id == bot.user.id and msg.embeds
                        and msg.embeds[0].title == PANEL_TITLE):
                    panel_messages.add((ch.id, msg.id))
                    if from_panel is None:
                        from_panel = parse_schedule_from_embed(msg.embeds[0])
        except Exception as e:
            logger.warning(f"#{ch.name} 패널 검색 실패: {e}")
 
    if not from_file:
        if from_panel:
            schedule = from_panel
            logger.info(f"[일정] 기존 패널에서 {len(schedule)}개 복원")
        else:
            schedule = dict(DEFAULT_SCHEDULE)
            logger.info("[일정] 기본 시간표 사용")
        sort_schedule()
        save_schedule()
 
    schedule_loaded = True
 
 
# ─────────────────────────────────────────
# 게시판 API 호출
# ─────────────────────────────────────────
async def fetch_board_posts(session: aiohttp.ClientSession, board: str, size: int = 10):
    info = BOARDS[board]
    url = (f"{API_BASE}/{info['api_key']}/article/search/moreArticle"
           f"?isVote=true&moreSize={size}&moreDirection=BEFORE&previousArticleId=0")
    try:
        async with session.get(url, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"{info['label']} 요청 실패: HTTP {resp.status}")
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logger.error(f"{info['label']} 불러오기 예외: {e}")
        return None
 
    posts = []
    for item in data.get("contentList", []):
        post_id = item.get("id")
        if post_id:
            posts.append({
                "id": post_id,
                "title": item.get("title") or "새 게시글",
                "url": info["view_url"].format(id=post_id),
            })
    return posts
 
 
# ─────────────────────────────────────────
# 임베드 생성 함수
# ─────────────────────────────────────────
def schedule_lines() -> str:
    if not schedule:
        return "등록된 일정이 없습니다. **⚙️ 일정 추가/수정** 버튼으로 추가해 주세요."
    return "\n".join(f"⏰ `{t}` ➔ {msg}" for t, msg in schedule.items())
 
 
def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title=PANEL_TITLE,
        description=schedule_lines(),
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="버튼 안내",
        value="🔄 시간표 새로고침 · ⚙️ 일정 추가/수정 · 🗑️ 일정 삭제\n"
              "📅 오늘의 일정 · 📢 최근 공지사항 · 🚀 최근 업데이트",
        inline=False,
    )
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    embed.set_footer(text=f"마지막 새로고침: {now} (KST)")
    return embed
 
 
def build_schedule_embed(title_prefix: str = "📅 [오늘의 일정]") -> discord.Embed:
    date_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    embed = discord.Embed(
        title=f"{title_prefix} {date_str}",
        description="아이온2 레기온원 여러분! 오늘의 알림 시간표입니다.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="⚔️ 시간표", value=schedule_lines()[:1024], inline=False)
    return embed
 
 
async def build_latest_embed(board: str) -> discord.Embed:
    info = BOARDS[board]
    async with aiohttp.ClientSession() as session:
        posts = await fetch_board_posts(session, board, size=5)
    embed = discord.Embed(title=f"📋 최근 {info['label']}", color=info["color"])
    if posts:
        embed.description = "\n".join(f"• [{p['title']}]({p['url']})" for p in posts)
    else:
        embed.description = "지금은 게시글을 불러올 수 없습니다. 잠시 후 다시 시도해 주세요."
    embed.add_field(name="게시판 바로가기", value=f"[{info['label']} 전체 보기]({info['list_url']})")
    return embed
 
 
def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🤖 장벽봇 도움말",
        description=f"24시간 자동으로 아이온2 알림을 `{AUTO_CHANNEL_NAME}` 채널에 전송합니다.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="명령어",
        value="`/패널생성` - 이 채널에 시간표 패널 설치 (관리자)\n"
              "`/일정` - 오늘의 알림 시간표\n"
              "`/도움말` - 봇 안내",
        inline=False,
    )
    return embed
 
 
async def refresh_all_panels(skip_message_id: int | None = None):
    """일정이 바뀌면 기억하고 있는 모든 패널을 최신 내용으로 갱신"""
    for ch_id, msg_id in list(panel_messages):
        if msg_id == skip_message_id:
            continue
        try:
            ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
            msg = await ch.fetch_message(msg_id)
            await msg.edit(embed=build_panel_embed(), view=PanelView())
        except discord.NotFound:
            panel_messages.discard((ch_id, msg_id))
        except Exception as e:
            logger.warning(f"패널 갱신 실패 ({ch_id}/{msg_id}): {e}")
 
 
def is_manager(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)
 
 
# ─────────────────────────────────────────
# 팝업창 (Modal)
# ─────────────────────────────────────────
class ScheduleEditModal(discord.ui.Modal, title="⚙️ 일정 추가/수정"):
    time_input = discord.ui.TextInput(
        label="시간 (HH:MM)",
        placeholder="20:55",
        max_length=5,
        required=True,
    )
    message_input = discord.ui.TextInput(
        label="알림 메시지 내용",
        style=discord.TextStyle.paragraph,
        placeholder="📢 [카이라] 5분 전 준비!",
        max_length=200,
        required=True,
    )
 
    def __init__(self, panel_message: discord.Message | None = None):
        super().__init__()
        self.panel_message = panel_message
 
    async def on_submit(self, interaction: discord.Interaction):
        t = normalize_time(self.time_input.value)
        if not t:
            await interaction.response.send_message(
                f"⚠️ 시간 형식이 잘못되었습니다: `{self.time_input.value}`\n"
                "`20:55`처럼 00:00 ~ 23:59 사이로 입력해 주세요.", ephemeral=True)
            return
 
        # 패널 한 줄 형식이 깨지지 않도록 줄바꿈은 공백으로
        msg = " ".join(self.message_input.value.split())
        action = "수정" if t in schedule else "추가"
        schedule[t] = msg
        sort_schedule()
        save_schedule()
        logger.info(f"[일정 {action}] {t} → {msg} (by {interaction.user})")
 
        await interaction.response.send_message(
            f"✅ 일정이 **{action}**되었습니다.\n⏰ `{t}` ➔ {msg}", ephemeral=True)
 
        if self.panel_message:
            try:
                await self.panel_message.edit(embed=build_panel_embed(), view=PanelView())
            except Exception as e:
                logger.warning(f"패널 즉시 갱신 실패: {e}")
        await refresh_all_panels(skip_message_id=self.panel_message.id if self.panel_message else None)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"일정 수정 팝업 오류: {error}")
        await send_error(interaction)
 
 
class ScheduleDeleteModal(discord.ui.Modal, title="🗑️ 일정 삭제"):
    time_input = discord.ui.TextInput(
        label="삭제할 시간 (HH:MM)",
        placeholder="20:55",
        max_length=5,
        required=True,
    )
 
    def __init__(self, panel_message: discord.Message | None = None):
        super().__init__()
        self.panel_message = panel_message
 
    async def on_submit(self, interaction: discord.Interaction):
        t = normalize_time(self.time_input.value)
        if not t or t not in schedule:
            await interaction.response.send_message(
                f"⚠️ `{self.time_input.value}` 시간에 등록된 일정이 없습니다.", ephemeral=True)
            return
 
        removed = schedule.pop(t)
        save_schedule()
        logger.info(f"[일정 삭제] {t} → {removed} (by {interaction.user})")
 
        await interaction.response.send_message(
            f"🗑️ 일정이 **삭제**되었습니다.\n⏰ `{t}` ➔ {removed}", ephemeral=True)
 
        if self.panel_message:
            try:
                await self.panel_message.edit(embed=build_panel_embed(), view=PanelView())
            except Exception as e:
                logger.warning(f"패널 즉시 갱신 실패: {e}")
        await refresh_all_panels(skip_message_id=self.panel_message.id if self.panel_message else None)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"일정 삭제 팝업 오류: {error}")
        await send_error(interaction)
 
 
async def send_error(interaction: discord.Interaction, msg: str = "⚠️ 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass
 
 
# ─────────────────────────────────────────
# 버튼 패널 (Persistent View: timeout=None + custom_id)
# ─────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    # ── 1줄: 시간표 관리 ──
    @discord.ui.button(label="일정 새로고침", emoji="🔄", row=0,
                       style=discord.ButtonStyle.primary, custom_id="btn_refresh_schedule")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_messages.add((interaction.channel_id, interaction.message.id))
        await interaction.response.edit_message(embed=build_panel_embed(), view=self)
 
    @discord.ui.button(label="일정 추가/수정", emoji="⚙️", row=0,
                       style=discord.ButtonStyle.success, custom_id="btn_edit_schedule")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_manager(interaction):
            await interaction.response.send_message(
                "⚠️ 일정 수정은 **서버 관리 권한**이 있는 사람만 할 수 있습니다.", ephemeral=True)
            return
        panel_messages.add((interaction.channel_id, interaction.message.id))
        await interaction.response.send_modal(ScheduleEditModal(interaction.message))
 
    @discord.ui.button(label="일정 삭제", emoji="🗑️", row=0,
                       style=discord.ButtonStyle.danger, custom_id="btn_delete_schedule")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_manager(interaction):
            await interaction.response.send_message(
                "⚠️ 일정 삭제는 **서버 관리 권한**이 있는 사람만 할 수 있습니다.", ephemeral=True)
            return
        panel_messages.add((interaction.channel_id, interaction.message.id))
        await interaction.response.send_modal(ScheduleDeleteModal(interaction.message))
 
    # ── 2줄: 정보 확인 (누른 사람에게만 보임) ──
    @discord.ui.button(label="오늘의 일정", emoji="📅", row=1,
                       style=discord.ButtonStyle.secondary, custom_id="btn_today_schedule")
    async def today_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_schedule_embed(), ephemeral=True)
 
    @discord.ui.button(label="최근 공지사항", emoji="📢", row=1,
                       style=discord.ButtonStyle.secondary, custom_id="btn_latest_notice")
    async def notice_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("notice"), ephemeral=True)
 
    @discord.ui.button(label="최근 업데이트", emoji="🚀", row=1,
                       style=discord.ButtonStyle.secondary, custom_id="btn_latest_update")
    async def update_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("update"), ephemeral=True)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"패널 버튼 오류 ({getattr(item, 'custom_id', item)}): {error}")
        await send_error(interaction)
 
 
# ─────────────────────────────────────────
# 봇 본체
# ─────────────────────────────────────────
class BarrierBot(commands.Bot):
    async def setup_hook(self):
        # 재시작 후에도 기존 패널 버튼이 먹통이 되지 않도록 등록
        self.add_view(PanelView())
 
        await asyncio.sleep(3)
        try:
            synced = await self.tree.sync()
            logger.info(f"슬래시 명령어 {len(synced)}개 동기화: {[c.name for c in synced]}")
        except discord.HTTPException as e:
            logger.warning(f"명령어 동기화 실패 (봇 구동은 유지됨): {e}")
        except Exception as e:
            logger.error(f"명령어 동기화 중 예외: {e}")
 
 
intents = discord.Intents.default()
bot = BarrierBot(command_prefix="!", intents=intents)
 
 
def get_target_channels():
    return [
        ch for guild in bot.guilds for ch in guild.text_channels
        if ch.name == AUTO_CHANNEL_NAME
    ]
 
 
async def send_to_targets(**kwargs):
    channels = get_target_channels()
    if not channels:
        logger.warning(f"'{AUTO_CHANNEL_NAME}' 채널을 찾을 수 없습니다.")
    for ch in channels:
        try:
            await ch.send(**kwargs)
        except discord.Forbidden:
            logger.warning(f"#{ch.name} ({ch.guild.name}) 채널에 메시지 권한이 없습니다.")
        except Exception as e:
            logger.error(f"#{ch.name} 전송 실패: {e}")
 
 
# ─────────────────────────────────────────
# 1. 신규 게시글 감시 (3분 주기)
# ─────────────────────────────────────────
@tasks.loop(minutes=3)
async def check_website_updates():
    try:
        async with aiohttp.ClientSession() as session:
            for board, info in BOARDS.items():
                posts = await fetch_board_posts(session, board, size=10)
                if not posts:
                    continue
 
                current_ids = {p["id"] for p in posts}
                if seen_ids[board] is None:
                    seen_ids[board] = current_ids
                    logger.info(f"[초기화] {info['label']} 기준글: {posts[0]['title']}")
                    continue
 
                new_posts = [p for p in posts if p["id"] not in seen_ids[board]]
                for p in reversed(new_posts):
                    embed = discord.Embed(
                        title=info["alert_title"],
                        description=f"**[{p['title']}]({p['url']})**",
                        color=info["color"],
                        timestamp=datetime.now(KST),
                    )
                    await send_to_targets(embed=embed)
                    logger.info(f"[알림] {info['label']} 새 글: {p['title']}")
 
                seen_ids[board] |= current_ids
    except Exception as e:
        logger.error(f"게시글 감시 루프 예외 (다음 주기에 재시도): {e}")
 
 
@check_website_updates.before_loop
async def before_website_updates():
    await bot.wait_until_ready()
 
 
# ─────────────────────────────────────────
# 2. 정기 일정 알림 (1분 주기, KST) — 패널에서 수정한 시간표를 그대로 사용
# ─────────────────────────────────────────
@tasks.loop(minutes=1)
async def check_schedule_alerts():
    global last_alert_time
    try:
        now_str = datetime.now(KST).strftime("%H:%M")
        if now_str == last_alert_time or now_str not in schedule:
            return
        last_alert_time = now_str
        await send_to_targets(content=f"⏰ **{now_str}** {schedule[now_str]}")
    except Exception as e:
        logger.error(f"일정 알림 루프 예외 (다음 주기에 재시도): {e}")
 
 
@check_schedule_alerts.before_loop
async def before_schedule_alerts():
    await bot.wait_until_ready()
 
 
@bot.event
async def on_ready():
    logger.info(f"봇 로그인 완료: {bot.user} (ID: {bot.user.id}) / 서버 {len(bot.guilds)}개")
    await restore_schedule()
    if not check_website_updates.is_running():
        check_website_updates.start()
    if not check_schedule_alerts.is_running():
        check_schedule_alerts.start()
 
 
# ─────────────────────────────────────────
# 슬래시 명령어
# ─────────────────────────────────────────
@bot.tree.command(name="패널생성", description="이 채널에 보스/콘텐츠 시간표 패널을 설치합니다. (관리자 전용)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def create_panel(interaction: discord.Interaction):
    channel = interaction.channel
    await interaction.response.defer(ephemeral=True, thinking=True)
 
    # 같은 채널의 예전 패널은 정리 → 패널 하나만 상주
    removed = 0
    try:
        async for msg in channel.history(limit=50):
            if (msg.author.id == bot.user.id and msg.embeds
                    and msg.embeds[0].title == PANEL_TITLE):
                await msg.delete()
                panel_messages.discard((channel.id, msg.id))
                removed += 1
    except discord.Forbidden:
        pass
 
    try:
        new_msg = await channel.send(embed=build_panel_embed(), view=PanelView())
        panel_messages.add((channel.id, new_msg.id))
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ 이 채널에 메시지를 보낼 권한이 없습니다. 봇 권한을 확인해 주세요.", ephemeral=True)
        return
 
    note = f" (이전 패널 {removed}개 정리)" if removed else ""
    tip = ""
    if channel.name != AUTO_CHANNEL_NAME:
        tip = (f"\n💡 패널을 `{AUTO_CHANNEL_NAME}` 채널에 설치해 두면, "
               "봇이 재시작돼도 수정한 일정이 그대로 복원됩니다.")
    await interaction.followup.send(f"✅ 패널을 설치했습니다.{note}{tip}", ephemeral=True)
 
 
@bot.tree.command(name="일정", description="오늘의 알림 시간표를 확인합니다.")
async def schedule_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_schedule_embed(), ephemeral=True)
 
 
@bot.tree.command(name="도움말", description="장벽봇 사용 방법을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)
 
 
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await send_error(interaction, "⚠️ 이 명령어는 서버 관리 권한이 있는 사람만 쓸 수 있습니다.")
    else:
        logger.error(f"명령어 오류: {error}")
        await send_error(interaction, "⚠️ 명령어 처리 중 오류가 발생했습니다.")
 
 
# ─────────────────────────────────────────
# 호스팅용 웹서버 (Render 포트 바인딩 / 외부 핑 응답)
# ─────────────────────────────────────────
async def start_web_server():
    app = web.Application()
 
    async def health(request):
        status = "ready" if bot.is_ready() else "starting"
        return web.Response(text=f"Bot Alive ({status})")
 
    app.router.add_get("/", health)   # GET 등록 시 HEAD도 자동 처리됨
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"웹 서버 시작: 0.0.0.0:{PORT}")
 
 
async def diagnose_discord():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v10/gateway",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                logger.info(f"[진단] Discord 접속 status={r.status}")
    except Exception as e:
        logger.error(f"[진단] Discord 접속 실패: {e}")
 
 
async def main():
    if not TOKEN:
        logger.error("DISCORD_TOKEN 환경변수가 없습니다.")
        return
 
    await start_web_server()
    await diagnose_discord()
    try:
        async with bot:
            await bot.start(TOKEN)
    except discord.HTTPException as e:
        if e.status == 429:
            logger.error("Discord 429 (IP 차단 추정) → 재시작 폭주 방지를 위해 1시간 대기 후 종료")
            await asyncio.sleep(3600)
        raise
 
 
if __name__ == "__main__":
    asyncio.run(main())
