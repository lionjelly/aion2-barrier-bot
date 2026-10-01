import os
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands, tasks
import aiohttp
from bs4 import BeautifulSoup
from aiohttp import web
 
# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("barrier_bot")
 
# 주요 설정
TOKEN = os.environ.get("DISCORD_TOKEN")
AUTO_CHANNEL_NAME = "🤖｜장벽봇"
KST = ZoneInfo("Asia/Seoul")
 
# 크롤링 타겟 URL
NOTICE_URL = "https://aion2.plaync.com/ko-kr/board/notice/list"
UPDATE_URL = "https://aion2.plaync.com/ko-kr/board/update/list"
 
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://aion2.plaync.com/"
}
 
# 메모리 상태 변수
last_notice_id = None
last_update_id = None
last_alert_time = ""
 
 
# 봇 클래스: 슬래시 명령어 동기화는 시작 시 1회만
class BarrierBot(commands.Bot):
    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            logger.info(f"슬래시 명령어 {len(synced)}개 동기화 완료.")
        except discord.HTTPException as e:
            logger.warning(f"명령어 동기화 실패 (봇 구동은 유지됨): {e}")
 
 
intents = discord.Intents.default()
bot = BarrierBot(command_prefix="!", intents=intents)
 
 
# Render 포트 바인딩용 웹서버
async def start_web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"웹 서버가 포트 {port}에서 정상 시작되었습니다.")
 
 
# 디스코드 접속 가능 여부 진단
async def diagnose_ip():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v10/gateway", timeout=10) as r:
                body = await r.text()
                logger.info(f"[진단] status={r.status} body={body[:200]}")
    except Exception as e:
        logger.error(f"[진단] 요청 실패: {e}")
 
 
# 대상 채널 검색
def get_target_channel():
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == AUTO_CHANNEL_NAME:
                return channel
    return None
 
 
# 공지사항 / 업데이트 크롤링
async def fetch_latest_board_item(session, url):
    try:
        async with session.get(url, headers=HEADERS, timeout=10) as resp:
            if resp.status != 200:
                logger.warning(f"게시판 요청 실패 ({url}): HTTP 상태 코드 {resp.status}")
                return None
 
            html = await resp.text()
            soup = BeautifulSoup(html, 'html.parser')
 
            items = soup.select('.board-list a, .list-item a, article a, table.board_list a')
            if not items:
                items = [a for a in soup.find_all('a', href=True) if '/board/' in a['href']]
 
            if items:
                first_item = items[0]
                href = first_item.get('href', '')
                title = first_item.get_text(strip=True) or "새 게시글"
 
                if not href.startswith("http"):
                    full_url = f"https://aion2.plaync.com{href}" if href.startswith("/") else f"https://aion2.plaync.com/{href}"
                else:
                    full_url = href
 
                return {"id": full_url, "title": title, "url": full_url}
    except Exception as e:
        logger.error(f"크롤링 중 예외 발생 ({url}): {e}")
    return None
 
 
# 1. 신규 게시글 모니터링 루프 (3분 주기)
@tasks.loop(minutes=3)
async def check_website_updates():
    global last_notice_id, last_update_id
    try:
        channel = get_target_channel()
        if not channel:
            return
 
        async with aiohttp.ClientSession() as session:
            notice = await fetch_latest_board_item(session, NOTICE_URL)
            if notice:
                if last_notice_id is None:
                    last_notice_id = notice['id']
                    logger.info(f"[초기화] 공지사항 기준글 설정: {notice['title']}")
                elif last_notice_id != notice['id']:
                    last_notice_id = notice['id']
                    embed = discord.Embed(
                        title="📢 [공지사항] 새 글이 등록되었습니다!",
                        description=f"**[{notice['title']}]({notice['url']})**",
                        color=discord.Color.blue()
                    )
                    await channel.send(embed=embed)
 
            update = await fetch_latest_board_item(session, UPDATE_URL)
            if update:
                if last_update_id is None:
                    last_update_id = update['id']
                    logger.info(f"[초기화] 업데이트 기준글 설정: {update['title']}")
                elif last_update_id != update['id']:
                    last_update_id = update['id']
                    embed = discord.Embed(
                        title="🚀 [업데이트] 새 패치노트가 등록되었습니다!",
                        description=f"**[{update['title']}]({update['url']})**",
                        color=discord.Color.green()
                    )
                    await channel.send(embed=embed)
 
    except Exception as e:
        logger.error(f"check_website_updates 루프 예외 발생: {e}")
 
 
# 2. 보스/콘텐츠 시각 알림 루프 (1분 주기)
@tasks.loop(minutes=1)
async def check_schedule_alerts():
    global last_alert_time
    try:
        now = datetime.now(KST)
        current_time_str = now.strftime("%H:%M")
 
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
 
 
@bot.tree.command(name="일정", description="오늘의 주요 주간 컨텐츠 일정을 확인합니다.")
async def schedule_command(interaction: discord.Interaction):
    now = datetime.now(KST)
    date_str = now.strftime("%Y년 %m월 %d일")
    embed = discord.Embed(
        title=f"📝 [오늘의 숙제] {date_str}",
        description="아이온2 레기온원 여러분! 오늘 저녁 진행되는 주요 주간 콘텐츠 일정입니다.",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="⚔️ 오늘 예정된 주요 주간 콘텐츠",
        value="• 카이라/아그로: 03:00 / 09:00 / 21:00\n• 어비스균열: 19:00 / 22:00",
        inline=False
    )
    await interaction.response.send_message(embed=embed)
 
 
@bot.tree.command(name="도움말", description="장벽봇 사용 방법을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 장벽봇 도움말",
        description="24시간 자동으로 아이온2 알림을 전송하는 봇입니다.",
        color=discord.Color.blue()
    )
    embed.add_field(name="명령어 목록", value="`/일정` - 오늘의 컨텐츠 일정 확인\n`/도움말` - 봇 안내 확인", inline=False)
    await interaction.response.send_message(embed=embed)
 
 
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
