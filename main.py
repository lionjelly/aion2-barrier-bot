
박현준, 연결됨

















Main · PY
import os
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands, tasks
import aiohttp
from aiohttp import web
 
# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("barrier_bot")
 
# 주요 설정
TOKEN = os.environ.get("DISCORD_TOKEN")
AUTO_CHANNEL_NAME = "🤖｜장벽봇"
KST = ZoneInfo("Asia/Seoul")
 
# PlayNC 게시판 API (공지/업데이트 목록은 이 API로 불러와야 실제 게시글이 나옴)
API_BASE = "https://api-community.plaync.com/aion2/board"
BOARDS = {
    "notice": {
        "api_key": "notice_ko",
        "view_url": "https://aion2.plaync.com/ko-kr/board/notice/view?articleId={id}",
        "label": "공지사항",
        "alert_title": "📢 [공지사항] 새 글이 등록되었습니다!",
        "color": discord.Color.blue(),
    },
    "update": {
        "api_key": "update_ko",
        "view_url": "https://aion2.plaync.com/ko-kr/board/update/view?articleId={id}",
        "label": "업데이트",
        "alert_title": "🚀 [업데이트] 새 패치노트가 등록되었습니다!",
        "color": discord.Color.green(),
    },
}
 
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://aion2.plaync.com",
    "Referer": "https://aion2.plaync.com/",
}
 
# 이미 본 게시글 ID 저장 (게시판별)
seen_ids = {"notice": None, "update": None}
last_alert_time = ""
 
SCHEDULE_TEXT = "• 카이라/아그로: 03:00 / 09:00 / 21:00\n• 어비스균열: 19:00 / 22:00"
 
 
# ─────────────────────────────────────────
# 게시판 API 호출
# ─────────────────────────────────────────
async def fetch_board_posts(session, board, size=10):
    info = BOARDS[board]
    url = (f"{API_BASE}/{info['api_key']}/article/search/moreArticle"
           f"?isVote=true&moreSize={size}&moreDirection=BEFORE&previousArticleId=0")
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"{info['label']} 요청 실패: HTTP {resp.status}")
                return None
            data = await resp.json(content_type=None)
            posts = []
            for item in data.get("contentList", []):
                post_id = item.get("id")
                if not post_id:
                    continue
                posts.append({
                    "id": post_id,
                    "title": item.get("title") or "새 게시글",
                    "url": info["view_url"].format(id=post_id),
                })
            return posts
    except Exception as e:
        logger.error(f"{info['label']} 불러오기 예외: {e}")
        return None
 
 
# ─────────────────────────────────────────
# 임베드 생성 함수 (명령어/패널 버튼 공용)
# ─────────────────────────────────────────
def build_schedule_embed():
    date_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    embed = discord.Embed(
        title=f"📝 [오늘의 숙제] {date_str}",
        description="아이온2 레기온원 여러분! 오늘 진행되는 주요 콘텐츠 일정입니다.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="⚔️ 오늘 예정된 주요 콘텐츠", value=SCHEDULE_TEXT, inline=False)
    return embed
 
 
def build_help_embed():
    embed = discord.Embed(
        title="🤖 장벽봇 도움말",
        description="24시간 자동으로 아이온2 알림을 전송하는 봇입니다.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="명령어 목록",
        value="`/패널` - 버튼 패널 열기\n`/일정` - 오늘의 콘텐츠 일정\n`/도움말` - 봇 안내",
        inline=False,
    )
    return embed
 
 
async def build_latest_embed(board):
    info = BOARDS[board]
    async with aiohttp.ClientSession() as session:
        posts = await fetch_board_posts(session, board, size=5)
    embed = discord.Embed(title=f"📋 최신 {info['label']}", color=info["color"])
    if not posts:
        embed.description = "지금은 게시글을 불러올 수 없습니다. 잠시 후 다시 시도해 주세요."
    else:
        embed.description = "\n".join(f"• [{p['title']}]({p['url']})" for p in posts)
    return embed
 
 
# ─────────────────────────────────────────
# /패널 버튼 (봇이 재시작돼도 버튼이 계속 동작하도록 timeout=None + custom_id)
# ─────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(label="오늘 일정", emoji="📝", style=discord.ButtonStyle.primary, custom_id="panel:schedule")
    async def schedule_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_schedule_embed(), ephemeral=True)
 
    @discord.ui.button(label="최신 공지", emoji="📢", style=discord.ButtonStyle.secondary, custom_id="panel:notice")
    async def notice_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("notice"), ephemeral=True)
 
    @discord.ui.button(label="최신 업데이트", emoji="🚀", style=discord.ButtonStyle.secondary, custom_id="panel:update")
    async def update_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await build_latest_embed("update"), ephemeral=True)
 
    @discord.ui.button(label="도움말", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="panel:help")
    async def help_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)
 
 
# ─────────────────────────────────────────
# 봇 본체
# ─────────────────────────────────────────
class BarrierBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(PanelView())
        try:
            synced = await self.tree.sync()
            logger.info(f"슬래시 명령어 {len(synced)}개 동기화 완료: {[c.name for c in synced]}")
        except discord.HTTPException as e:
            logger.warning(f"명령어 동기화 실패 (봇 구동은 유지됨): {e}")
 
 
intents = discord.Intents.default()
bot = BarrierBot(command_prefix="!", intents=intents)
 
 
async def start_web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"웹 서버가 포트 {port}에서 정상 시작되었습니다.")
 
 
async def diagnose_ip():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v10/gateway", timeout=aiohttp.ClientTimeout(total=10)) as r:
                body = await r.text()
                logger.info(f"[진단] status={r.status} body={body[:200]}")
    except Exception as e:
        logger.error(f"[진단] 요청 실패: {e}")
 
 
def get_target_channel():
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == AUTO_CHANNEL_NAME:
                return channel
    return None
 
 
# 1. 신규 게시글 모니터링 루프 (3분 주기)
@tasks.loop(minutes=3)
async def check_website_updates():
    try:
        channel = get_target_channel()
        if not channel:
            logger.warning(f"'{AUTO_CHANNEL_NAME}' 채널을 찾을 수 없습니다.")
            return
 
        async with aiohttp.ClientSession() as session:
            for board, info in BOARDS.items():
                posts = await fetch_board_posts(session, board, size=10)
                if not posts:
                    continue
 
                current_ids = {p["id"] for p in posts}
 
                # 첫 실행: 지금 있는 글들을 기준으로 저장만 함
                if seen_ids[board] is None:
                    seen_ids[board] = current_ids
                    logger.info(f"[초기화] {info['label']} 기준글 설정: {posts[0]['title']}")
                    continue
 
                # 처음 보는 글만 알림 (오래된 글부터 순서대로)
                new_posts = [p for p in posts if p["id"] not in seen_ids[board]]
                for p in reversed(new_posts):
                    embed = discord.Embed(
                        title=info["alert_title"],
                        description=f"**[{p['title']}]({p['url']})**",
                        color=info["color"],
                    )
                    await channel.send(embed=embed)
                    logger.info(f"[알림] {info['label']} 새 글: {p['title']}")
 
                seen_ids[board] |= current_ids
 
    except Exception as e:
        logger.error(f"check_website_updates 루프 예외 발생: {e}")
 
 
# 2. 보스/콘텐츠 시각 알림 루프 (1분 주기)
@tasks.loop(minutes=1)
async def check_schedule_alerts():
    global last_alert_time
    try:
        current_time_str = datetime.now(KST).strftime("%H:%M")
        if current_time_str == last_alert_time:
            return
 
        channel = get_target_channel()
        if not channel:
            return
 
        schedule_events = {
            "02:55": "📢 **[아그로/카이라]** 5분 후에 시작됩니다! (03:00 시작)",
            "08:55": "📢 **[카이라]** 5분 후에 시작됩니다! (09:00 시작)",
            "18:50": "📢 **[어비스균열]** 10분 후에 시작됩니다! (19:00 시작)",
            "19:00": "📝 **[오늘의 숙제]** 매일 19시 일일 요약\n아이온2 주요 컨텐츠 진행 일정입니다.",
            "20:55": "📢 **[카이라]** 5분 후에 시작됩니다! (21:00 시작)",
            "21:50": "📢 **[어비스균열]** 10분 후에 시작됩니다! (22:00 시작)"
        }
 
        if current_time_str in schedule_events:
            last_alert_time = current_time_str
            await channel.send(schedule_events[current_time_str])
 
    except Exception as e:
        logger.error(f"check_schedule_alerts 루프 예외 발생: {e}")
 
 
@bot.event
async def on_ready():
    logger.info(f"봇 로그인 완료: {bot.user.name} (ID: {bot.user.id})")
    if not check_website_updates.is_running():
        check_website_updates.start()
    if not check_schedule_alerts.is_running():
        check_schedule_alerts.start()
 
 
# ─────────────────────────────────────────
# 슬래시 명령어
# ─────────────────────────────────────────
@bot.tree.command(name="패널", description="장벽봇 버튼 패널을 엽니다.")
async def panel_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ 장벽봇 패널",
        description="아래 버튼을 눌러 원하는 정보를 확인하세요.\n(결과는 누른 사람에게만 보입니다)",
        color=discord.Color.purple(),
    )
    await interaction.response.send_message(embed=embed, view=PanelView())
 
 
@bot.tree.command(name="일정", description="오늘의 주요 콘텐츠 일정을 확인합니다.")
async def schedule_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_schedule_embed())
 
 
@bot.tree.command(name="도움말", description="장벽봇 사용 방법을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed())
 
 
async def main():
    await start_web_server()
    await diagnose_ip()
    try:
        async with bot:
            await bot.start(TOKEN)
    except discord.HTTPException as e:
        if e.status == 429:
            logger.error("429 발생 → 1시간 대기 후 종료합니다.")
            await asyncio.sleep(3600)
        raise
 
 
if __name__ == "__main__":
    asyncio.run(main())
 
