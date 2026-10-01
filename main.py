import os
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
# 기본 설정import os
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
PANEL_TITLE = "🛡️ 장벽봇 패널"
 
# ─────────────────────────────────────────
# PlayNC 게시판
# 게시판 페이지(list)는 글 목록을 자바스크립트로 불러오기 때문에
# HTML을 긁으면 글이 안 보임 → 페이지가 실제로 쓰는 API를 직접 호출
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
 
# 403 차단 방지용 브라우저 헤더
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://aion2.plaync.com",
    "Referer": "https://aion2.plaync.com/",
}
 
# 게시판별 이미 본 글 ID (None = 아직 기준글 저장 전)
seen_ids = {"notice": None, "update": None}
last_alert_time = ""
 
# ─────────────────────────────────────────
# 일정 데이터 (여기만 고치면 알림/패널/명령어 모두 반영)
# ─────────────────────────────────────────
SCHEDULE_TEXT = (
    "• 아그로/카이라: 03:00 / 09:00 / 21:00\n"
    "• 어비스균열: 19:00 / 22:00"
)
 
SCHEDULE_ALERTS = {
    "02:55": "📢 **[아그로/카이라]** 5분 후에 시작됩니다! (03:00 시작)",
    "08:55": "📢 **[아그로/카이라]** 5분 후에 시작됩니다! (09:00 시작)",
    "18:50": "📢 **[어비스균열]** 10분 후에 시작됩니다! (19:00 시작)",
    "20:55": "📢 **[아그로/카이라]** 5분 후에 시작됩니다! (21:00 시작)",
    "21:50": "📢 **[어비스균열]** 10분 후에 시작됩니다! (22:00 시작)",
}
DAILY_SUMMARY_TIME = "19:00"
 
 
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
# 임베드 (명령어 / 버튼 / 알림 공용)
# ─────────────────────────────────────────
def build_schedule_embed(title_prefix: str = "📝 [오늘의 숙제]") -> discord.Embed:
    date_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    embed = discord.Embed(
        title=f"{title_prefix} {date_str}",
        description="아이온2 레기온원 여러분! 오늘 진행되는 주요 콘텐츠 일정입니다.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="⚔️ 주요 콘텐츠", value=SCHEDULE_TEXT, inline=False)
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
 
 
def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title=PANEL_TITLE,
        description="아래 버튼을 눌러 원하는 정보를 확인하세요.\n"
                    "결과는 **누른 사람에게만** 보입니다.",
        color=discord.Color.purple(),
    )
    embed.add_field(name="📅 오늘의 일정 확인", value="오늘 주요 콘텐츠 시간표", inline=True)
    embed.add_field(name="📢 최근 공지사항", value="최신 공지 5개", inline=True)
    embed.add_field(name="🚀 최근 업데이트", value="최신 패치노트 5개", inline=True)
    embed.set_footer(text="장벽봇 · 24시간 자동 알림")
    return embed
 
 
def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🤖 장벽봇 도움말",
        description=f"24시간 자동으로 아이온2 알림을 `{AUTO_CHANNEL_NAME}` 채널에 전송합니다.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="명령어",
        value="`/패널생성` - 이 채널에 버튼 패널 설치 (관리자)\n"
              "`/일정` - 오늘의 콘텐츠 일정\n"
              "`/도움말` - 봇 안내",
        inline=False,
    )
    return embed
 
 
# ─────────────────────────────────────────
# 버튼 패널
# timeout=None + custom_id → 봇이 재시작돼도 기존 패널 버튼이 계속 동작
# ─────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(label="오늘의 일정 확인", emoji="📅",
                       style=discord.ButtonStyle.primary, custom_id="barrier:schedule")
    async def schedule_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_schedule_embed(), ephemeral=True)
 
    @discord.ui.button(label="최근 공지사항", emoji="📢",
                       style=discord.ButtonStyle.secondary, custom_id="barrier:notice")
    async def notice_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 게시판 불러오는 데 시간이 걸릴 수 있어 먼저 "생각 중" 표시
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("notice"), ephemeral=True)
 
    @discord.ui.button(label="최근 업데이트", emoji="🚀",
                       style=discord.ButtonStyle.secondary, custom_id="barrier:update")
    async def update_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("update"), ephemeral=True)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"패널 버튼 오류 ({item}): {error}")
        msg = "⚠️ 처리 중 오류가 발생했습니다. 잠시 후 다시 눌러 주세요."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass
 
 
# ─────────────────────────────────────────
# 봇 본체
# ─────────────────────────────────────────
class BarrierBot(commands.Bot):
    async def setup_hook(self):
        # 재시작 후에도 기존 패널 버튼이 동작하도록 등록
        self.add_view(PanelView())
 
        # 명령어 동기화는 프로세스당 1회만 (on_ready는 재연결 때마다 다시 불려서 부적합)
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
    """봇이 들어가 있는 모든 서버에서 알림 채널을 찾음"""
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
 
                # 첫 가동: 현재 글들을 기준으로 저장만 하고 알림은 보내지 않음
                if seen_ids[board] is None:
                    seen_ids[board] = current_ids
                    logger.info(f"[초기화] {info['label']} 기준글: {posts[0]['title']}")
                    continue
 
                # 처음 보는 글만, 오래된 순서대로 알림
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
# 2. 정기 일정 알림 (1분 주기, KST)
# ─────────────────────────────────────────
@tasks.loop(minutes=1)
async def check_schedule_alerts():
    global last_alert_time
    try:
        now_str = datetime.now(KST).strftime("%H:%M")
        if now_str == last_alert_time:
            return
 
        if now_str == DAILY_SUMMARY_TIME:
            last_alert_time = now_str
            await send_to_targets(embed=build_schedule_embed("📝 [오늘의 숙제 요약]"))
        elif now_str in SCHEDULE_ALERTS:
            last_alert_time = now_str
            await send_to_targets(content=SCHEDULE_ALERTS[now_str])
    except Exception as e:
        logger.error(f"일정 알림 루프 예외 (다음 주기에 재시도): {e}")
 
 
@check_schedule_alerts.before_loop
async def before_schedule_alerts():
    await bot.wait_until_ready()
 
 
@bot.event
async def on_ready():
    logger.info(f"봇 로그인 완료: {bot.user} (ID: {bot.user.id}) / 서버 {len(bot.guilds)}개")
    if not check_website_updates.is_running():
        check_website_updates.start()
    if not check_schedule_alerts.is_running():
        check_schedule_alerts.start()
 
 
# ─────────────────────────────────────────
# 슬래시 명령어
# ─────────────────────────────────────────
@bot.tree.command(name="패널생성", description="이 채널에 장벽봇 버튼 패널을 설치합니다. (관리자 전용)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def create_panel(interaction: discord.Interaction):
    channel = interaction.channel
    await interaction.response.defer(ephemeral=True, thinking=True)
 
    # 같은 채널의 예전 패널은 지워서 패널이 하나만 상주하도록
    removed = 0
    try:
        async for msg in channel.history(limit=50):
            if (msg.author.id == bot.user.id and msg.embeds
                    and msg.embeds[0].title == PANEL_TITLE):
                await msg.delete()
                removed += 1
    except discord.Forbidden:
        pass  # 기록 보기/삭제 권한 없으면 그냥 새 패널만 보냄
 
    try:
        await channel.send(embed=build_panel_embed(), view=PanelView())
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ 이 채널에 메시지를 보낼 권한이 없습니다. 봇 권한을 확인해 주세요.", ephemeral=True)
        return
 
    note = f" (이전 패널 {removed}개 정리)" if removed else ""
    await interaction.followup.send(f"✅ 패널을 설치했습니다.{note}", ephemeral=True)
 
 
@bot.tree.command(name="일정", description="오늘의 주요 콘텐츠 일정을 확인합니다.")
async def schedule_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_schedule_embed(), ephemeral=True)
 
 
@bot.tree.command(name="도움말", description="장벽봇 사용 방법을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)
 
 
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "⚠️ 이 명령어는 서버 관리 권한이 있는 사람만 쓸 수 있습니다."
    else:
        logger.error(f"명령어 오류: {error}")
        msg = "⚠️ 명령어 처리 중 오류가 발생했습니다."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass
 
 
# ─────────────────────────────────────────
# 호스팅용 웹서버 (Render 포트 바인딩 / 외부 핑 응답)
# ─────────────────────────────────────────
async def start_web_server():
    app = web.Application()
 
    async def health(request):
        status = "ready" if bot.is_ready() else "starting"
        return web.Response(text=f"Bot Alive ({status})")
 
    app.router.add_get("/", health)
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
 
    await start_web_server()   # 로그인보다 먼저 포트를 열어 배포 실패/재시작 반복 방지
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
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("barrier_bot")
 
TOKEN = os.environ.get("DISCORD_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
KST = ZoneInfo(os.environ.get("TZ", "Asia/Seoul"))
AUTO_CHANNEL_NAME = "🤖｜장벽봇"
PANEL_TITLE = "🛡️ 장벽봇 패널"
 
# ─────────────────────────────────────────
# PlayNC 게시판
# 게시판 페이지(list)는 글 목록을 자바스크립트로 불러오기 때문에
# HTML을 긁으면 글이 안 보임 → 페이지가 실제로 쓰는 API를 직접 호출
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
 
# 403 차단 방지용 브라우저 헤더
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://aion2.plaync.com",
    "Referer": "https://aion2.plaync.com/",
}
 
# 게시판별 이미 본 글 ID (None = 아직 기준글 저장 전)
seen_ids = {"notice": None, "update": None}
last_alert_time = ""
 
# ─────────────────────────────────────────
# 일정 데이터 (여기만 고치면 알림/패널/명령어 모두 반영)
# ─────────────────────────────────────────
SCHEDULE_TEXT = (
    "• 아그로/카이라: 03:00 / 09:00 / 21:00\n"
    "• 어비스균열: 19:00 / 22:00"
)
 
SCHEDULE_ALERTS = {
    "02:55": "📢 **[아그로/카이라]** 5분 후에 시작됩니다! (03:00 시작)",
    "08:55": "📢 **[아그로/카이라]** 5분 후에 시작됩니다! (09:00 시작)",
    "18:50": "📢 **[어비스균열]** 10분 후에 시작됩니다! (19:00 시작)",
    "20:55": "📢 **[아그로/카이라]** 5분 후에 시작됩니다! (21:00 시작)",
    "21:50": "📢 **[어비스균열]** 10분 후에 시작됩니다! (22:00 시작)",
}
DAILY_SUMMARY_TIME = "19:00"
 
 
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
# 임베드 (명령어 / 버튼 / 알림 공용)
# ─────────────────────────────────────────
def build_schedule_embed(title_prefix: str = "📝 [오늘의 숙제]") -> discord.Embed:
    date_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    embed = discord.Embed(
        title=f"{title_prefix} {date_str}",
        description="아이온2 레기온원 여러분! 오늘 진행되는 주요 콘텐츠 일정입니다.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="⚔️ 주요 콘텐츠", value=SCHEDULE_TEXT, inline=False)
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
 
 
def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title=PANEL_TITLE,
        description="아래 버튼을 눌러 원하는 정보를 확인하세요.\n"
                    "결과는 **누른 사람에게만** 보입니다.",
        color=discord.Color.purple(),
    )
    embed.add_field(name="📅 오늘의 일정 확인", value="오늘 주요 콘텐츠 시간표", inline=True)
    embed.add_field(name="📢 최근 공지사항", value="최신 공지 5개", inline=True)
    embed.add_field(name="🚀 최근 업데이트", value="최신 패치노트 5개", inline=True)
    embed.set_footer(text="장벽봇 · 24시간 자동 알림")
    return embed
 
 
def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🤖 장벽봇 도움말",
        description=f"24시간 자동으로 아이온2 알림을 `{AUTO_CHANNEL_NAME}` 채널에 전송합니다.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="명령어",
        value="`/패널생성` - 이 채널에 버튼 패널 설치 (관리자)\n"
              "`/일정` - 오늘의 콘텐츠 일정\n"
              "`/도움말` - 봇 안내",
        inline=False,
    )
    return embed
 
 
# ─────────────────────────────────────────
# 버튼 패널
# timeout=None + custom_id → 봇이 재시작돼도 기존 패널 버튼이 계속 동작
# ─────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(label="오늘의 일정 확인", emoji="📅",
                       style=discord.ButtonStyle.primary, custom_id="barrier:schedule")
    async def schedule_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_schedule_embed(), ephemeral=True)
 
    @discord.ui.button(label="최근 공지사항", emoji="📢",
                       style=discord.ButtonStyle.secondary, custom_id="barrier:notice")
    async def notice_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 게시판 불러오는 데 시간이 걸릴 수 있어 먼저 "생각 중" 표시
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("notice"), ephemeral=True)
 
    @discord.ui.button(label="최근 업데이트", emoji="🚀",
                       style=discord.ButtonStyle.secondary, custom_id="barrier:update")
    async def update_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("update"), ephemeral=True)
 
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        logger.error(f"패널 버튼 오류 ({item}): {error}")
        msg = "⚠️ 처리 중 오류가 발생했습니다. 잠시 후 다시 눌러 주세요."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass
 
 
# ─────────────────────────────────────────
# 봇 본체
# ─────────────────────────────────────────
class BarrierBot(commands.Bot):
    async def setup_hook(self):
        # 재시작 후에도 기존 패널 버튼이 동작하도록 등록
        self.add_view(PanelView())
 
        # 명령어 동기화는 프로세스당 1회만 (on_ready는 재연결 때마다 다시 불려서 부적합)
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
    """봇이 들어가 있는 모든 서버에서 알림 채널을 찾음"""
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
 
                # 첫 가동: 현재 글들을 기준으로 저장만 하고 알림은 보내지 않음
                if seen_ids[board] is None:
                    seen_ids[board] = current_ids
                    logger.info(f"[초기화] {info['label']} 기준글: {posts[0]['title']}")
                    continue
 
                # 처음 보는 글만, 오래된 순서대로 알림
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
# 2. 정기 일정 알림 (1분 주기, KST)
# ─────────────────────────────────────────
@tasks.loop(minutes=1)
async def check_schedule_alerts():
    global last_alert_time
    try:
        now_str = datetime.now(KST).strftime("%H:%M")
        if now_str == last_alert_time:
            return
 
        if now_str == DAILY_SUMMARY_TIME:
            last_alert_time = now_str
            await send_to_targets(embed=build_schedule_embed("📝 [오늘의 숙제 요약]"))
        elif now_str in SCHEDULE_ALERTS:
            last_alert_time = now_str
            await send_to_targets(content=SCHEDULE_ALERTS[now_str])
    except Exception as e:
        logger.error(f"일정 알림 루프 예외 (다음 주기에 재시도): {e}")
 
 
@check_schedule_alerts.before_loop
async def before_schedule_alerts():
    await bot.wait_until_ready()
 
 
@bot.event
async def on_ready():
    logger.info(f"봇 로그인 완료: {bot.user} (ID: {bot.user.id}) / 서버 {len(bot.guilds)}개")
    if not check_website_updates.is_running():
        check_website_updates.start()
    if not check_schedule_alerts.is_running():
        check_schedule_alerts.start()
 
 
# ─────────────────────────────────────────
# 슬래시 명령어
# ─────────────────────────────────────────
@bot.tree.command(name="패널생성", description="이 채널에 장벽봇 버튼 패널을 설치합니다. (관리자 전용)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def create_panel(interaction: discord.Interaction):
    channel = interaction.channel
    await interaction.response.defer(ephemeral=True, thinking=True)
 
    # 같은 채널의 예전 패널은 지워서 패널이 하나만 상주하도록
    removed = 0
    try:
        async for msg in channel.history(limit=50):
            if (msg.author.id == bot.user.id and msg.embeds
                    and msg.embeds[0].title == PANEL_TITLE):
                await msg.delete()
                removed += 1
    except discord.Forbidden:
        pass  # 기록 보기/삭제 권한 없으면 그냥 새 패널만 보냄
 
    try:
        await channel.send(embed=build_panel_embed(), view=PanelView())
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ 이 채널에 메시지를 보낼 권한이 없습니다. 봇 권한을 확인해 주세요.", ephemeral=True)
        return
 
    note = f" (이전 패널 {removed}개 정리)" if removed else ""
    await interaction.followup.send(f"✅ 패널을 설치했습니다.{note}", ephemeral=True)
 
 
@bot.tree.command(name="일정", description="오늘의 주요 콘텐츠 일정을 확인합니다.")
async def schedule_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_schedule_embed(), ephemeral=True)
 
 
@bot.tree.command(name="도움말", description="장벽봇 사용 방법을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)
 
 
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "⚠️ 이 명령어는 서버 관리 권한이 있는 사람만 쓸 수 있습니다."
    else:
        logger.error(f"명령어 오류: {error}")
        msg = "⚠️ 명령어 처리 중 오류가 발생했습니다."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass
 
 
# ─────────────────────────────────────────
# 호스팅용 웹서버 (Render 포트 바인딩 / 외부 핑 응답)
# ─────────────────────────────────────────
async def start_web_server():
    app = web.Application()
 
    async def health(request):
        status = "ready" if bot.is_ready() else "starting"
        return web.Response(text=f"Bot Alive ({status})")
 
    app.router.add_get("/", health)
    app.router.add_head("/", health)
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
 
    await start_web_server()   # 로그인보다 먼저 포트를 열어 배포 실패/재시작 반복 방지
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
