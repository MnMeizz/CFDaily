# -*- coding: utf-8 -*-
import random
import aiohttp
import datetime
import re
from bs4 import BeautifulSoup
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("astrbot_plugin_cf_daily", "YourName", "Codeforces 每日一题插件", "2.0.3")
class CFDailyPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.daily_limit = config.get("daily_limit", 1) if config else 1

    # --- 数据存储辅助方法 ---
    def _get_user_key(self, user_id: str) -> str:
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        return f"cf_daily_{user_id}_{today_str}"

    async def _get_user_usage(self, user_id: str) -> int:
        key = self._get_user_key(user_id)
        val = await self.get_kv_data(key, 0)
        return int(val) if val else 0

    async def _increment_user_usage(self, user_id: str):
        key = self._get_user_key(user_id)
        current = await self._get_user_usage(user_id)
        await self.put_kv_data(key, str(current + 1))

    async def _check_quota(self, user_id: str) -> tuple:
        used = await self._get_user_usage(user_id)
        remaining = self.daily_limit - used
        return remaining > 0, remaining

    # --- 获取题目列表 ---
    async def fetch_problemset(self):
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

    # --- 直接抓取题目页面获取详细描述 ---
    async def fetch_problem_statement(self, contest_id: int, index: str):
        """抓取并解析题目详情页面，返回结构化的题面数据"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
        mirror_url = f"https://mirror.codeforces.com/problemset/problem/{contest_id}/{index}"

        async with aiohttp.ClientSession(headers=headers) as session:
            # 尝试主站，失败则尝试镜像站
            html = None
            for try_url in (url, mirror_url):
                try:
                    async with session.get(try_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            break
                except Exception:
                    continue

            if html is None:
                logger.error(f"无法获取题目页面 {contest_id}{index}")
                return None

        # 解析 HTML
        soup = BeautifulSoup(html, 'html.parser')

        # 标题
        title_tag = soup.find('div', class_='title')
        title = title_tag.text.strip() if title_tag else f"{contest_id}{index}"

        # 时间/内存限制
        time_limit = "N/A"
        memory_limit = "N/A"
        time_limit_tag = soup.find('div', class_='time-limit')
        if time_limit_tag:
            time_limit = time_limit_tag.text.replace('time limit per test', '').strip()
        memory_limit_tag = soup.find('div', class_='memory-limit')
        if memory_limit_tag:
            memory_limit = memory_limit_tag.text.replace('memory limit per test', '').strip()

        # 题目内容主体
        problem_statement = soup.find('div', class_='problem-statement')
        if not problem_statement:
            logger.error("未找到题目内容区域")
            return None

        # 提取描述、输入、输出、备注（HTML 格式保留以便后续处理）
        description_html = ""
        input_spec_html = ""
        output_spec_html = ""
        note_html = ""

        # 直接根据 class 定位
        header = problem_statement.find('div', class_='header')
        # 描述通常在 header 之后，没有特定 class，我们通过位置关系来提取
        # 常见结构: header, 然后是多个无 class 的 div 作为描述，接着是 input-specification, output-specification, note
        # 为了简单可靠，我们使用 class 选择器
        desc_div = None
        for div in problem_statement.find_all('div', recursive=False):
            if 'header' in div.get('class', []):
                continue
            if 'input-specification' in div.get('class', []):
                input_spec_html = str(div)
            elif 'output-specification' in div.get('class', []):
                output_spec_html = str(div)
            elif 'note' in div.get('class', []):
                note_html = str(div)
            elif 'sample-tests' not in div.get('class', []) and desc_div is None:
                # 第一个既不是 header 也不是 spec/note/sample 的 div 视为描述
                desc_div = div

        if desc_div:
            description_html = str(desc_div)

        # 提取样例
        sample_tests = []
        sample_inputs = problem_statement.find_all('div', class_='input')
        sample_outputs = problem_statement.find_all('div', class_='output')
        for i in range(min(len(sample_inputs), len(sample_outputs))):
            inp_pre = sample_inputs[i].find('pre')
            out_pre = sample_outputs[i].find('pre')
            if inp_pre and out_pre:
                sample_tests.append({
                    "input": inp_pre.get_text('\n').strip(),
                    "output": out_pre.get_text('\n').strip()
                })

        return {
            "title": title,
            "time_limit": time_limit,
            "memory_limit": memory_limit,
            "description": description_html,
            "input_spec": input_spec_html,
            "output_spec": output_spec_html,
            "sample_tests": sample_tests,
            "note": note_html
        }

    # --- 渲染图片并发送 ---
    async def _render_and_send(self, event: AstrMessageEvent, problem: dict):
        contest_id = problem.get("contestId")
        index = problem.get("index")
        problem_url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
        tags = ", ".join(problem.get("tags", []))
        rating = problem.get("rating", "未知")

        # 修正方法名调用
        statement = await self.fetch_problem_statement(contest_id, index)
        if not statement:
            yield event.plain_result(f"获取详细题面失败，仅显示基础信息：\n标题: {problem.get('name')}\n难度: {rating}\n链接: {problem_url}")
            return

        def clean_text(text):
            if not text:
                return ""
            text = re.sub(r'<[^>]+>', '', text)
            text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            return text.strip()

        description = clean_text(statement["description"])
        input_spec = clean_text(statement["input_spec"])
        output_spec = clean_text(statement["output_spec"])
        note = clean_text(statement["note"])

        samples_html = ""
        if statement["sample_tests"]:
            samples_html = '<div class="section-title">样例</div>'
            for i, sample in enumerate(statement["sample_tests"]):
                samples_html += f'''
                <div class="sample-block">
                    <div class="sample-title">输入 #{i+1}</div>
                    <pre>{sample["input"]}</pre>
                    <div class="sample-title">输出 #{i+1}</div>
                    <pre>{sample["output"]}</pre>
                </div>
                '''
        note_html = ""
        if note:
            note_html = f'<div class="section-title">备注</div><div class="note-content">{note}</div>'

        tmpl = f'''
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8">
        <style>
            body {{ font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; background: #fff; padding: 30px; max-width: 900px; margin: 0 auto; color: #24292e; }}
            .header {{ border-bottom: 2px solid #e1e4e8; padding-bottom: 15px; margin-bottom: 20px; }}
            .title {{ font-size: 28px; font-weight: bold; margin-bottom: 5px; }}
            .subtitle {{ color: #586069; font-size: 16px; }}
            .info-bar {{ display: flex; gap: 30px; background: #f6f8fa; padding: 12px 20px; border-radius: 8px; margin: 20px 0; }}
            .info-item {{ display: flex; flex-direction: column; }}
            .info-label {{ font-size: 12px; color: #6a737d; text-transform: uppercase; }}
            .info-value {{ font-size: 18px; font-weight: 600; }}
            .section-title {{ font-size: 20px; font-weight: bold; margin: 25px 0 10px 0; border-bottom: 1px solid #eaecef; padding-bottom: 5px; }}
            pre {{ background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px; padding: 12px; font-family: monospace; font-size: 14px; overflow-x: auto; white-space: pre-wrap; }}
            .sample-block {{ margin-bottom: 20px; }}
            .sample-title {{ font-weight: 600; margin: 10px 0 5px 0; }}
            .note-content {{ background: #f8f9fa; padding: 15px; border-left: 4px solid #6a737d; border-radius: 0 6px 6px 0; }}
            .tags {{ margin-top: 25px; padding-top: 15px; border-top: 1px solid #e1e4e8; }}
            .tag {{ display: inline-block; background: #e1e4e8; padding: 4px 12px; margin: 0 8px 8px 0; border-radius: 20px; font-size: 14px; }}
            .footer {{ margin-top: 20px; text-align: right; color: #6a737d; font-size: 13px; }}
            a {{ color: #0366d6; text-decoration: none; }}
        </style>
        </head>
        <body>
            <div class="header">
                <div class="title">{statement["title"]}</div>
                <div class="subtitle">Codeforces {contest_id}{index} · 难度分: {rating}</div>
            </div>
            <div class="info-bar">
                <div class="info-item"><span class="info-label">时间限制</span><span class="info-value">{statement["time_limit"]}</span></div>
                <div class="info-item"><span class="info-label">内存限制</span><span class="info-value">{statement["memory_limit"]}</span></div>
            </div>
            <div class="section-title">题目描述</div><div>{description}</div>
            <div class="section-title">输入格式</div><div>{input_spec}</div>
            <div class="section-title">输出格式</div><div>{output_spec}</div>
            {samples_html}
            {note_html}
            <div class="tags"><strong>标签：</strong><br>{" ".join([f'<span class="tag">{tag}</span>' for tag in tags.split(", ")])}</div>
            <div class="footer"><a href="{problem_url}">在 Codeforces 中查看原题</a></div>
        </body>
        </html>
        '''

        try:
            url = await self.html_render(tmpl, {})
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"渲染图片失败: {e}")
            yield event.plain_result(f"图片生成失败，请直接访问：{problem_url}")

    @filter.command("每日一题")
    async def daily_cf(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        has_quota, remaining = await self._check_quota(user_id)
        if not has_quota:
            yield event.plain_result(f"您今日的每日一题次数已用完（每日 {self.daily_limit} 次），请明天再来。")
            return

        problems = await self.fetch_problemset()
        if problems is None:
            yield event.plain_result("获取题目列表失败，请稍后再试。")
            return

        filtered = [p for p in problems if "rating" in p]
        if not filtered:
            yield event.plain_result("暂时没有合适的题目，请稍后再试。")
            return

        problem = random.choice(filtered)
        await self._increment_user_usage(user_id)

        name = problem.get("name", "未知标题")
        rating = problem.get("rating", "未知")
        tags = ", ".join(problem.get("tags", []))
        yield event.plain_result(f"今日一题已送达！\n标题: {name}\n难度分: {rating}\n标签: {tags}\n剩余次数: {remaining-1}/{self.daily_limit}")

        async for result in self._render_and_send(event, problem):
            yield result

    async def terminate(self):
        pass