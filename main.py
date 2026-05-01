# -*- coding: utf-8 -*-
import random
import aiohttp
import datetime
import re
from bs4 import BeautifulSoup
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register("astrbot_plugin_cf_daily", "YourName", "Codeforces 每日一题插件", "2.1.0")
class CFDailyPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.daily_limit = config.get("daily_limit", 1) if config else 1
        self.admin_id = config.get("admin_id", None) if config else None

    # ==================== 数据存储 ====================
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

    # ==================== Codeforces API ====================
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

    # ==================== 剥离 CF 自带的 section-title ====================
    def _strip_cf_section_titles(self, html_text):
        """移除 Codeforces HTML 中自带的 <div class="section-title">...</div>，
        避免与模板中自定义的标题重复显示。"""
        if not html_text:
            return html_text
        soup = BeautifulSoup(html_text, 'html.parser')
        for div in soup.find_all('div', class_='section-title'):
            div.decompose()
        return str(soup)

    # ==================== 数学公式保护 ====================
    def _protect_math(self, text: str) -> tuple:
        if not text:
            return "", []
        formulas = []

        def _replace(match):
            formulas.append(match.group(0))
            return f"MATHX{len(formulas) - 1}X"

        text = re.sub(r'\$\$(.+?)\$\$', _replace, text, flags=re.DOTALL)
        text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', _replace, text, flags=re.DOTALL)
        text = re.sub(r'\\$$(.+?)\\$$', _replace, text, flags=re.DOTALL)
        text = re.sub(r'\\$$(.+?)\\$$', _replace, text, flags=re.DOTALL)
        return text, formulas

    def _restore_math(self, text: str, formulas: list) -> str:
        if not text or not formulas:
            return text or ""
        for i, formula in enumerate(formulas):
            text = text.replace(f"MATHX{i}X", formula)
        return text

    # ==================== LLM 翻译 ====================
    async def _translate_to_chinese(self, description: str, input_spec: str,
                                     output_spec: str, note: str):
        try:
            provider = self.context.get_using_provider()
            if not provider:
                logger.warning("未配置 LLM 提供者，跳过翻译")
                return None
        except Exception as e:
            logger.warning(f"获取 LLM 提供者失败: {e}")
            return None

        desc_safe, desc_fx = self._protect_math(description)
        input_safe, input_fx = self._protect_math(input_spec)
        output_safe, output_fx = self._protect_math(output_spec)
        note_safe, note_fx = self._protect_math(note)

        combined = (
            f"<<<DESC_START>>>\n{desc_safe.strip()}\n<<<DESC_END>>>\n"
            f"<<<INPUT_START>>>\n{input_safe.strip()}\n<<<INPUT_END>>>\n"
            f"<<<OUTPUT_START>>>\n{output_safe.strip()}\n<<<OUTPUT_END>>>\n"
            f"<<<NOTE_START>>>\n{note_safe.strip()}\n<<<NOTE_END>>>"
        )

        system_prompt = (
            "你是 Codeforces 竞赛题目翻译助手，将英文题目 HTML 内容翻译为中文。\n"
            "严格要求：\n"
            "1. 保留所有 MATHX0, MATHX1 等占位符原样不变\n"
            "2. 保留所有 HTML 标签原样不变（<p>, <ul>, <li>, <div>, <span>, <br>, <b>, <i> 等）\n"
            "3. 只翻译 HTML 标签外的自然语言文本为中文\n"
            "4. 保留 <<<xxx_START>>> 和 <<<xxx_END>>> 分隔符原样不变\n"
            "5. 算法/竞赛术语使用常见中文译法（如 dynamic programming → 动态规划）\n"
            "6. 不要添加任何额外解释，只输出翻译后的带分隔符内容"
        )
        prompt = f"请翻译以下 Codeforces 题目内容：\n\n{combined}"

        try:
            try:
                response = await provider.text_chat(
                    prompt=prompt, contexts=[], system_prompt=system_prompt
                )
            except TypeError:
                full_prompt = f"[系统指令]\n{system_prompt}\n\n[用户内容]\n{prompt}"
                response = await provider.text_chat(prompt=full_prompt, contexts=[])

            if hasattr(response, 'completion_text'):
                result_text = response.completion_text
            elif isinstance(response, str):
                result_text = response
            else:
                result_text = str(response)

            translated = {}
            formulas_map = {
                "DESC": desc_fx, "INPUT": input_fx,
                "OUTPUT": output_fx, "NOTE": note_fx,
            }
            for key in ["DESC", "INPUT", "OUTPUT", "NOTE"]:
                pattern = rf'<<<{key}_START>>>(.*?)<<<{key}_END>>>'
                match = re.search(pattern, result_text, re.DOTALL)
                if match:
                    translated[key] = self._restore_math(
                        match.group(1).strip(), formulas_map[key]
                    )
                else:
                    translated[key] = ""

            logger.info("题目翻译完成")
            return translated

        except Exception as e:
            logger.error(f"LLM 翻译失败: {e}")
            return None

    # ==================== 渲染并发送 ====================
    async def _render_and_send(self, event: AstrMessageEvent, problem: dict):
        contest_id = problem.get("contestId")
        index = problem.get("index")
        problem_url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
        tags = ", ".join(problem.get("tags", []))
        rating = problem.get("rating", "未知")

        statement = await self.fetch_problem_statement(contest_id, index)
        if not statement:
            yield event.plain_result(
                f"获取详细题面失败，仅显示基础信息：\n"
                f"标题: {problem.get('name')}\n难度: {rating}\n链接: {problem_url}"
            )
            return

        def process_cf_html(text):
            if not text:
                return ""
            text = re.sub(r'\$\$\$(.*?)\$\$\$', r'\\(\1\\)', text, flags=re.DOTALL)
            return text

        # ★ 核心修复：剥离 CF 自带的 section-title，再处理公式格式
        description = process_cf_html(statement["description"])
        input_spec = process_cf_html(self._strip_cf_section_titles(statement["input_spec"]))
        output_spec = process_cf_html(self._strip_cf_section_titles(statement["output_spec"]))
        note = process_cf_html(self._strip_cf_section_titles(statement["note"]))

        # 样例 HTML
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
                </div>'''

        tags_html = " ".join(
            [f'<span class="tag">{t.strip()}</span>' for t in tags.split(",") if t.strip()]
        )

        # 尝试翻译
        translated = await self._translate_to_chinese(
            description, input_spec, output_spec, note
        )
        has_translation = translated and any(
            v.strip() for v in translated.values()
        )

        # ============ 双栏模板 ============
        if has_translation:
            desc_cn = translated.get("DESC", "")
            input_cn = translated.get("INPUT", "")
            output_cn = translated.get("OUTPUT", "")
            note_cn = translated.get("NOTE", "")

            note_html_en = (
                f'<div class="section-title">备注</div>'
                f'<div class="note-content cf-content">{note}</div>' if note else ""
            )
            note_html_cn = (
                f'<div class="section-title">备注</div>'
                f'<div class="note-content cf-content">{note_cn}</div>' if note_cn else ""
            )

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
                            processEscapes: true, processEnvironments: true
                        }},
                        svg: {{ fontCache: 'global' }},
                        options: {{ ignoreHtmlClass: 'no-mathjax' }}
                    }};
                </script>
                <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" id="MathJax-script" async></script>
                <style>
                    html, body {{ margin: 0; padding: 0; background-color: #f2f5f8; }}
                    body {{
                        font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
                        color: #24292e; box-sizing: border-box;
                        line-height: 1.6; padding: 40px;
                    }}
                    .glass-container {{
                        background: rgba(255,255,255,0.65);
                        backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
                        border-radius: 20px;
                        border: 1px solid rgba(255,255,255,0.8);
                        box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                        padding: 40px; max-width: 1400px; margin: 0 auto;
                    }}
                    .header {{
                        border-bottom: 2px solid rgba(0,0,0,0.05);
                        padding-bottom: 15px; margin-bottom: 20px;
                    }}
                    .title {{ font-size: 28px; font-weight: bold; margin-bottom: 5px; }}
                    .subtitle {{ color: #586069; font-size: 16px; }}
                    .info-bar {{
                        display: flex; gap: 30px;
                        background: rgba(255,255,255,0.5);
                        padding: 12px 20px; border-radius: 12px; margin: 20px 0;
                        border: 1px solid rgba(255,255,255,0.6);
                    }}
                    .info-item {{ display: flex; flex-direction: column; }}
                    .info-label {{ font-size: 12px; color: #6a737d; text-transform: uppercase; }}
                    .info-value {{ font-size: 18px; font-weight: 600; }}
                    .dual-container {{
                        display: grid; grid-template-columns: 1fr 1fr;
                        gap: 0; margin: 25px 0;
                        border-radius: 12px; overflow: hidden;
                        border: 1px solid rgba(0,0,0,0.06);
                    }}
                    .panel {{ padding: 25px; min-width: 0; }}
                    .panel-en {{
                        background: rgba(255,255,255,0.3);
                        border-right: 2px solid rgba(0,0,0,0.06);
                    }}
                    .panel-cn {{ background: rgba(255,255,255,0.15); }}
                    .panel-badge {{
                        display: inline-block; padding: 4px 16px;
                        border-radius: 20px; font-size: 13px;
                        font-weight: 600; margin-bottom: 18px;
                        letter-spacing: 0.5px;
                    }}
                    .badge-en {{ background: rgba(59,130,246,0.1); color: #3b82f6; }}
                    .badge-cn {{ background: rgba(239,68,68,0.1); color: #ef4444; }}
                    .section-title {{
                        font-size: 18px; font-weight: bold;
                        margin: 22px 0 8px 0;
                        border-bottom: 1px solid rgba(0,0,0,0.05);
                        padding-bottom: 4px;
                    }}
                    .panel .section-title:first-of-type {{ margin-top: 0; }}
                    .cf-content p {{ margin: 0 0 12px 0; font-size: 14px; }}
                    .cf-content ul, .cf-content ol {{ margin: 0 0 12px 0; padding-left: 22px; }}
                    .cf-content li {{ margin-bottom: 4px; font-size: 14px; }}
                    pre {{
                        background: rgba(255,255,255,0.7);
                        border: 1px solid rgba(255,255,255,0.8);
                        border-radius: 8px; padding: 12px;
                        font-family: "Consolas", monospace;
                        font-size: 13px; overflow-x: auto; white-space: pre-wrap;
                    }}
                    .sample-block {{ margin-bottom: 18px; }}
                    .sample-title {{ font-weight: 600; margin: 8px 0 4px 0; }}
                    .note-content {{
                        background: rgba(255,255,255,0.6); padding: 12px;
                        border-left: 4px solid #8e9db0; border-radius: 0 8px 8px 0;
                    }}
                    .samples-section {{
                        margin-top: 25px; padding-top: 15px;
                        border-top: 2px solid rgba(0,0,0,0.06);
                    }}
                    .tags {{
                        margin-top: 20px; padding-top: 15px;
                        border-top: 1px solid rgba(0,0,0,0.05);
                    }}
                    .tag {{
                        display: inline-block;
                        background: rgba(255,255,255,0.8);
                        padding: 5px 12px; margin: 0 6px 6px 0;
                        border-radius: 20px; font-size: 13px;
                        border: 1px solid rgba(255,255,255,0.9);
                        box-shadow: 0 2px 5px rgba(0,0,0,0.04);
                    }}
                    .translate-note {{
                        text-align: right; font-size: 11px;
                        color: #9ca3af; margin-top: 15px;
                    }}
                </style>
            </head>
            <body>
                <div class="glass-container">
                    <div class="header">
                        <div class="title">{statement["title"]}</div>
                        <div class="subtitle">Codeforces {contest_id}{index} · 难度分: {rating}</div>
                    </div>
                    <div class="info-bar">
                        <div class="info-item">
                            <span class="info-label">时间限制</span>
                            <span class="info-value">{statement["time_limit"]}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">内存限制</span>
                            <span class="info-value">{statement["memory_limit"]}</span>
                        </div>
                    </div>
                    <div class="dual-container">
                        <div class="panel panel-en">
                            <span class="panel-badge badge-en">English</span>
                            <div class="section-title">Description</div>
                            <div class="cf-content">{description}</div>
                            <div class="section-title">Input</div>
                            <div class="cf-content">{input_spec}</div>
                            <div class="section-title">Output</div>
                            <div class="cf-content">{output_spec}</div>
                            {note_html_en}
                        </div>
                        <div class="panel panel-cn">
                            <span class="panel-badge badge-cn">中文翻译</span>
                            <div class="section-title">题目描述</div>
                            <div class="cf-content">{desc_cn}</div>
                            <div class="section-title">输入</div>
                            <div class="cf-content">{input_cn}</div>
                            <div class="section-title">输出</div>
                            <div class="cf-content">{output_cn}</div>
                            {note_html_cn}
                        </div>
                    </div>
                    <div class="samples-section">
                        {samples_html}
                    </div>
                    <div class="tags">
                        <strong>标签：</strong><br>{tags_html}
                    </div>
                    <div class="translate-note">中文翻译由 LLM 自动生成，仅供参考</div>
                </div>
            </body>
            </html>
            '''

        # ============ 单栏回退模板 ============
        else:
            note_html = ""
            if note:
                note_html = (
                    f'<div class="section-title">备注</div>'
                    f'<div class="note-content cf-content">{note}</div>'
                )

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
                            processEscapes: true, processEnvironments: true
                        }},
                        svg: {{ fontCache: 'global' }},
                        options: {{ ignoreHtmlClass: 'no-mathjax' }}
                    }};
                </script>
                <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" id="MathJax-script" async></script>
                <style>
                    html, body {{ margin: 0; padding: 0; background-color: #f2f5f8; }}
                    body {{
                        font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
                        color: #24292e; box-sizing: border-box;
                        line-height: 1.6; padding: 40px;
                    }}
                    .glass-container {{
                        background: rgba(255,255,255,0.65);
                        backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
                        border-radius: 20px;
                        border: 1px solid rgba(255,255,255,0.8);
                        box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                        padding: 40px; max-width: 900px; margin: 0 auto;
                    }}
                    .header {{
                        border-bottom: 2px solid rgba(0,0,0,0.05);
                        padding-bottom: 15px; margin-bottom: 20px;
                    }}
                    .title {{ font-size: 28px; font-weight: bold; margin-bottom: 5px; }}
                    .subtitle {{ color: #586069; font-size: 16px; }}
                    .info-bar {{
                        display: flex; gap: 30px;
                        background: rgba(255,255,255,0.5);
                        padding: 12px 20px; border-radius: 12px; margin: 20px 0;
                        border: 1px solid rgba(255,255,255,0.6);
                    }}
                    .info-item {{ display: flex; flex-direction: column; }}
                    .info-label {{ font-size: 12px; color: #6a737d; text-transform: uppercase; }}
                    .info-value {{ font-size: 18px; font-weight: 600; }}
                    .section-title {{
                        font-size: 20px; font-weight: bold;
                        margin: 25px 0 10px 0;
                        border-bottom: 1px solid rgba(0,0,0,0.05);
                        padding-bottom: 5px;
                    }}
                    .cf-content p {{ margin: 0 0 15px 0; }}
                    .cf-content ul, .cf-content ol {{ margin: 0 0 15px 0; padding-left: 25px; }}
                    .cf-content li {{ margin-bottom: 5px; }}
                    pre {{
                        background: rgba(255,255,255,0.7);
                        border: 1px solid rgba(255,255,255,0.8);
                        border-radius: 8px; padding: 15px;
                        font-family: "Consolas", monospace;
                        font-size: 14px; overflow-x: auto; white-space: pre-wrap;
                        box-shadow: inset 0 2px 4px rgba(0,0,0,0.02);
                    }}
                    .sample-block {{ margin-bottom: 20px; }}
                    .sample-title {{ font-weight: 600; margin: 10px 0 5px 0; }}
                    .note-content {{
                        background: rgba(255,255,255,0.6); padding: 15px;
                        border-left: 4px solid #8e9db0; border-radius: 0 8px 8px 0;
                    }}
                    .tags {{
                        margin-top: 25px; padding-top: 15px;
                        border-top: 1px solid rgba(0,0,0,0.05);
                    }}
                    .tag {{
                        display: inline-block;
                        background: rgba(255,255,255,0.8);
                        padding: 6px 14px; margin: 0 8px 8px 0;
                        border-radius: 20px; font-size: 14px;
                        border: 1px solid rgba(255,255,255,0.9);
                        box-shadow: 0 2px 5px rgba(0,0,0,0.04);
                    }}
                </style>
            </head>
            <body>
                <div class="glass-container">
                    <div class="header">
                        <div class="title">{statement["title"]}</div>
                        <div class="subtitle">Codeforces {contest_id}{index} · 难度分: {rating}</div>
                    </div>
                    <div class="info-bar">
                        <div class="info-item">
                            <span class="info-label">时间限制</span>
                            <span class="info-value">{statement["time_limit"]}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">内存限制</span>
                            <span class="info-value">{statement["memory_limit"]}</span>
                        </div>
                    </div>
                    <div class="section-title">题目描述</div>
                    <div class="cf-content">{description}</div>
                    <div class="section-title">输入格式</div>
                    <div class="cf-content">{input_spec}</div>
                    <div class="section-title">输出格式</div>
                    <div class="cf-content">{output_spec}</div>
                    {samples_html}
                    {note_html}
                    <div class="tags">
                        <strong>标签：</strong><br>{tags_html}
                    </div>
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

    # ==================== 管理员重置 ====================
    @filter.command("cf重置")
    async def reset_daily(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        if self.admin_id is not None and user_id != self.admin_id:
            yield event.plain_result("❌ 权限不足，只有管理员可以重置。")
            return
        key = self._get_user_key(user_id)
        await self.put_kv_data(key, "0")
        yield event.plain_result("✅ 今日使用次数已重置为 0，可以继续使用「每日一题」了。")

    # ==================== 每日一题 ====================
    @filter.command("每日一题")
    async def daily_cf(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        has_quota, remaining = await self._check_quota(user_id)
        if not has_quota:
            yield event.plain_result(
                f"您今日的每日一题次数已用完（每日 {self.daily_limit} 次），请明天再来。"
            )
            return

        problems = await self.fetch_problemset()
        if problems is None:
            yield event.plain_result("获取题目列表失败，请稍后再试。")
            return

        message = event.message_str.strip()
        parts = message.split()
        a, b = None, None
        if len(parts) >= 3:
            try:
                a = int(parts[1])
                b = int(parts[2])
            except ValueError:
                pass

        if a is not None and b is not None and a <= b:
            filtered = [p for p in problems if "rating" in p and a <= p["rating"] <= b]
            range_desc = f"难度 {a}~{b}"
        else:
            filtered = [p for p in problems if "rating" in p]
            range_desc = "任意难度"

        if not filtered:
            yield event.plain_result(
                f"在 {range_desc} 区间内暂时没有合适的题目，请稍后再试或调整范围。"
            )
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
            f"今日一题已送达！\n标题: {name}\n难度分: {rating}\n标签: {tags}\n"
            f"链接：{problem_url}\n剩余次数: {remaining-1}/{self.daily_limit}"
        )

        async for result in self._render_and_send(event, problem):
            yield result

    async def terminate(self):
        pass
