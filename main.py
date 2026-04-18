# -*- coding: utf-8 -*-
import random
import aiohttp
import datetime
import re
from bs4 import BeautifulSoup
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("astrbot_plugin_cf_daily", "YourName", "Codeforces 每日一题插件", "2.0.8")
class CFDailyPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.daily_limit = config.get("daily_limit", 1) if config else 1
        # 读取管理员 ID（可配置为字符串，如 "123456"）
        self.admin_id = config.get("admin_id", None) if config else None

    # --- 数据存储辅助方法（完全不变）---
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

    # --- 获取题目列表（完全不变）---
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

    # --- 抓取题面（完全不变）---
    async def fetch_problem_statement(self, contest_id: int, index: str):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
        mirror_url = f"https://mirror.codeforces.com/problemset/problem/{contest_id}/{index}"

        async with aiohttp.ClientSession(headers=headers) as session:
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

        soup = BeautifulSoup(html, 'html.parser')

        title_tag = soup.find('div', class_='title')
        title = title_tag.text.strip() if title_tag else f"{contest_id}{index}"

        time_limit = "N/A"
        memory_limit = "N/A"
        time_limit_tag = soup.find('div', class_='time-limit')
        if time_limit_tag:
            time_limit = time_limit_tag.text.replace('time limit per test', '').strip()
        memory_limit_tag = soup.find('div', class_='memory-limit')
        if memory_limit_tag:
            memory_limit = memory_limit_tag.text.replace('memory limit per test', '').strip()

        problem_statement = soup.find('div', class_='problem-statement')
        if not problem_statement:
            logger.error("未找到题目内容区域")
            return None

        description_html = ""
        input_spec_html = ""
        output_spec_html = ""
        note_html = ""

        header = problem_statement.find('div', class_='header')
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
                desc_div = div

        if desc_div:
            description_html = str(desc_div)

        sample_tests = []
        sample_blocks = problem_statement.find_all('div', class_='sample-test')
        if not sample_blocks:
            sample_inputs = problem_statement.find_all('div', class_='input')
            sample_outputs = problem_statement.find_all('div', class_='output')
            for inp_div, out_div in zip(sample_inputs, sample_outputs):
                inp_pre = inp_div.find('pre')
                out_pre = out_div.find('pre')
                if inp_pre and out_pre:
                    sample_tests.append({
                        "input": inp_pre.get_text('\n').strip(),
                        "output": out_pre.get_text('\n').strip()
                    })
        else:
            for block in sample_blocks:
                inp_div = block.find('div', class_='input')
                out_div = block.find('div', class_='output')
                if inp_div and out_div:
                    inp_pre = inp_div.find('pre')
                    out_pre = out_div.find('pre')
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

    # --- 渲染图片并发送（背景样式已修复，其他不变）---
    async def _render_and_send(self, event: AstrMessageEvent, problem: dict):
        contest_id = problem.get("contestId")
        index = problem.get("index")
        problem_url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
        tags = ", ".join(problem.get("tags", []))
        rating = problem.get("rating", "未知")

        statement = await self.fetch_problem_statement(contest_id, index)
        if not statement:
            yield event.plain_result(f"获取详细题面失败，仅显示基础信息：\n标题: {problem.get('name')}\n难度: {rating}\n链接: {problem_url}")
            return

        def process_cf_html(text):
            if not text:
                return ""
            text = re.sub(r'\$\$\$(.*?)\$\$\$', r'\\(\1\\)', text, flags=re.DOTALL)
            return text

        description = process_cf_html(statement["description"])
        input_spec = process_cf_html(statement["input_spec"])
        output_spec = process_cf_html(statement["output_spec"])
        note = process_cf_html(statement["note"])

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
            note_html = f'<div class="section-title">备注</div><div class="note-content cf-content">{note}</div>'

        bg_color = "#f2f5f8"

        tmpl = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <script>
                window.MathJax = {{
                    tex: {{
                        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
                        processEscapes: true,
                        processEnvironments: true
                    }},
                    svg: {{ fontCache: 'global' }},
                    options: {{ ignoreHtmlClass: 'no-mathjax' }}
                }};
            </script>
            <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" id="MathJax-script" async></script>
            <style>
                html, body {{
                    margin: 0;
                    padding: 0;
                    min-height: 100vh;
                    background-color: {bg_color};
                }}
                body {{ 
                    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; 
                    color: #24292e; 
                    box-sizing: border-box;
                    line-height: 1.6;
                    padding: 40px;
                }}
                .glass-container {{
                    background: rgba(255, 255, 255, 0.65);
                    backdrop-filter: blur(16px);
                    -webkit-backdrop-filter: blur(16px);
                    border-radius: 20px;
                    border: 1px solid rgba(255, 255, 255, 0.8);
                    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1);
                    padding: 40px; 
                    max-width: 900px; 
                    margin: 0 auto; 
                }}
                .header {{ border-bottom: 2px solid rgba(0, 0, 0, 0.05); padding-bottom: 15px; margin-bottom: 20px; }}
                .title {{ font-size: 28px; font-weight: bold; margin-bottom: 5px; text-shadow: 0 1px 2px rgba(255,255,255,0.8); }}
                .subtitle {{ color: #586069; font-size: 16px; }}
                .info-bar {{ display: flex; gap: 30px; background: rgba(255, 255, 255, 0.5); padding: 12px 20px; border-radius: 12px; margin: 20px 0; border: 1px solid rgba(255, 255, 255, 0.6); }}
                .info-item {{ display: flex; flex-direction: column; }}
                .info-label {{ font-size: 12px; color: #6a737d; text-transform: uppercase; }}
                .info-value {{ font-size: 18px; font-weight: 600; }}
                .section-title {{ font-size: 20px; font-weight: bold; margin: 25px 0 10px 0; border-bottom: 1px solid rgba(0, 0, 0, 0.05); padding-bottom: 5px; }}
                
                .cf-content p {{ margin: 0 0 15px 0; }}
                .cf-content ul, .cf-content ol {{ margin: 0 0 15px 0; padding-left: 25px; }}
                .cf-content li {{ margin-bottom: 5px; }}
                
                pre {{ background: rgba(255, 255, 255, 0.7); border: 1px solid rgba(255, 255, 255, 0.8); border-radius: 8px; padding: 15px; font-family: "Consolas", monospace; font-size: 14px; overflow-x: auto; white-space: pre-wrap; box-shadow: inset 0 2px 4px rgba(0,0,0,0.02); }}
                .sample-block {{ margin-bottom: 20px; }}
                .sample-title {{ font-weight: 600; margin: 10px 0 5px 0; }}
                .note-content {{ background: rgba(255, 255, 255, 0.6); padding: 15px; border-left: 4px solid #8e9db0; border-radius: 0 8px 8px 0; }}
                .tags {{ margin-top: 25px; padding-top: 15px; border-top: 1px solid rgba(0, 0, 0, 0.05); }}
                .tag {{ display: inline-block; background: rgba(255, 255, 255, 0.8); padding: 6px 14px; margin: 0 8px 8px 0; border-radius: 20px; font-size: 14px; border: 1px solid rgba(255,255,255,0.9); box-shadow: 0 2px 5px rgba(0,0,0,0.04); }}
            </style>
        </head>
        <body>
            <div class="glass-container">
                <div class="header">
                    <div class="title">{statement["title"]}</div>
                    <div class="subtitle">Codeforces {contest_id}{index} · 难度分: {rating}</div>
                </div>
                <div class="info-bar">
                    <div class="info-item"><span class="info-label">时间限制</span><span class="info-value">{statement["time_limit"]}</span></div>
                    <div class="info-item"><span class="info-label">内存限制</span><span class="info-value">{statement["memory_limit"]}</span></div>
                </div>
                <div class="section-title">题目描述</div><div class="cf-content">{description}</div>
                <div class="section-title">输入格式</div><div class="cf-content">{input_spec}</div>
                <div class="section-title">输出格式</div><div class="cf-content">{output_spec}</div>
                {samples_html}
                {note_html}
                <div class="tags"><strong>标签：</strong><br>{" ".join([f'<span class="tag">{tag}</span>' for tag in tags.split(", ")])}</div>
            </div>
        </body>
        </html>
        '''

        try:
            url = await self.html_render(tmpl, {})
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"渲染图片失败: {e}")
            yield event.plain_result(f"图片生成失败，请直接访问：{problem_url}")

    # --- 新增：重置当日使用次数（管理员或通用）---
    @filter.command("cf重置")
    async def reset_daily(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        # 如果配置了 admin_id，则仅允许该管理员使用
        if self.admin_id is not None and user_id != self.admin_id:
            yield event.plain_result("❌ 权限不足，只有管理员可以重置。")
            return

        key = self._get_user_key(user_id)
        await self.put_kv_data(key, "0")
        yield event.plain_result("✅ 今日使用次数已重置为 0，可以继续使用「每日一题」了。")

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

        # 解析命令参数（支持 "每日一题 a b" 格式）
        message = event.message_str.strip()
        parts = message.split()
        a, b = None, None
        if len(parts) >= 3:  # 格式：/每日一题 a b
            try:
                a = int(parts[1])
                b = int(parts[2])
            except ValueError:
                pass  # 转换失败则按无参数处理

        # 根据是否有有效区间参数进行过滤
        if a is not None and b is not None and a <= b:
            filtered = [p for p in problems if "rating" in p and a <= p["rating"] <= b]
            range_desc = f"难度 {a}~{b}"
        else:
            filtered = [p for p in problems if "rating" in p]
            range_desc = "任意难度"

        if not filtered:
            yield event.plain_result(f"在 {range_desc} 区间内暂时没有合适的题目，请稍后再试或调整范围。")
            return

        problem = random.choice(filtered)
        await self._increment_user_usage(user_id)

        name = problem.get("name", "未知标题")
        rating = problem.get("rating", "未知")
        tags = ", ".join(problem.get("tags", []))
        contest_id = problem.get("contestId")
        index = problem.get("index")
        problem_url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
        yield event.plain_result(
            f"今日一题已送达！\n标题: {name}\n难度分: {rating}\n标签: {tags}\n链接：{problem_url}\n剩余次数: {remaining-1}/{self.daily_limit}"
        )

        async for result in self._render_and_send(event, problem):
            yield result

    async def terminate(self):
        pass