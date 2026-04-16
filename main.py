import random
import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("astrbot_plugin_cf_random", "YourName", "随机推荐Codeforces题目", "1.0.0")
class CFRandomPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def fetch_problems(self):
        url = "https://codeforces.com/api/problemset.problems"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    if data.get("status") == "OK":
                        return data["result"]["problems"]
                    else:
                        logger.error(f"Codeforces API error: {data.get('comment')}")
                        return None
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return None

    @filter.command("random_cf")
    async def random_cf(self, event: AstrMessageEvent, min_rating: int = 800, max_rating: int = 3500):
        if min_rating < 0 or max_rating < 0:
            yield event.plain_result("❌ 分数不能为负数。")
            return
        if min_rating > max_rating:
            yield event.plain_result("❌ 最低分不能大于最高分。")
            return

        problems = await self.fetch_problems()
        if problems is None:
            yield event.plain_result("😵 获取题目列表失败，请稍后再试～")
            return

        filtered = [
            p for p in problems
            if "rating" in p and min_rating <= p["rating"] <= max_rating
        ]
        if not filtered:
            yield event.plain_result(f"😕 在 {min_rating}~{max_rating} 分之间没有找到题目。")
            return

        problem = random.choice(filtered)
        name = problem.get("name", "未知标题")
        rating = problem.get("rating", "未知")
        contest_id = problem.get("contestId")
        index = problem.get("index")
        url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"

        reply = (
            f"🎲 随机Codeforces题目推荐\n"
            f"📌 标题: {name}\n"
            f"⭐ 难度分: {rating}\n"
            f"🔗 链接: {url}"
        )
        yield event.plain_result(reply)

    async def terminate(self):
        pass