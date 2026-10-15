"""OpenRouter calls with per-stage retries, and the section-by-section digest generation."""
import hashlib
import json
import os
import re
import time

from openai import OpenAI

from digest_schema import CATEGORIES, normalized_title, text, validate_editorial, validate_section
from newsgen import settings
from newsgen.prompts import (SYSTEM_PROMPT, build_overview_prompt, build_section_prompt,
                             model_view, resolve_aliases)


class TruncatedResponse(ValueError):
    pass


def request_json(client, request, run, prompt_builder, validator, output_tokens, checkpoint=None, aliases=None):
    """Retry only this stage. On length, raise its budget and ask for less visible output."""
    feedback = ""
    compact = False
    for attempt in range(settings.CONFIG["generation_attempts"]):
        prompt = prompt_builder(compact)
        detail = {
            "stage": request["stage"], "category": request.get("category"),
            "attempt": attempt + 1, "prompt": prompt, "feedback": feedback,
            "max_tokens": output_tokens, "reasoning_effort": settings.CONFIG.get("reasoning_effort"),
            "temperature": settings.CONFIG.get("temperature"), "response_format": settings.CONFIG.get("response_format"),
        }
        if aliases:
            detail["source_aliases"] = aliases
        request["attempts"].append(detail)
        run["attempts"].append(detail)
        # Hash a manifest of the exact user prompts and request controls, including retries.
        run["prompt"] = json.dumps([
            {"stage": r["stage"], "category": r.get("category"), "system_prompt": SYSTEM_PROMPT,
             "attempts": [{key: a[key] for key in ("prompt", "feedback", "max_tokens", "reasoning_effort")} for a in r["attempts"]]}
            for r in run["requests"]
        ], ensure_ascii=False, separators=(',', ':'))
        run["prompt_sha256"] = hashlib.sha256(run["prompt"].encode()).hexdigest()
        if checkpoint:
            checkpoint()
        started = time.monotonic()
        try:
            options = {}
            if settings.CONFIG.get("reasoning_effort"):
                options["extra_body"] = {"reasoning": {"effort": settings.CONFIG["reasoning_effort"]}}
            if settings.CONFIG.get("response_format"):
                options["response_format"] = {"type": settings.CONFIG["response_format"]}
            if settings.CONFIG.get("temperature") is not None:
                options["temperature"] = settings.CONFIG["temperature"]
            response = client.chat.completions.create(
                model=settings.MODEL, max_tokens=output_tokens,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": prompt + feedback}],
                **options,
            )
            detail["response_id"] = response.id
            detail["response_model"] = response.model
            detail["usage"] = response.usage.model_dump() if response.usage else None
            if not response.choices:
                raise ValueError("响应没有候选内容")
            choice = response.choices[0]
            detail["finish_reason"] = choice.finish_reason
            detail["native_finish_reason"] = getattr(choice, "native_finish_reason", None)
            result = choice.message.content or ""
            detail["raw_response"] = result
            if choice.finish_reason == "length":
                raise TruncatedResponse("响应达到输出长度限制（length）")
            if choice.finish_reason != "stop":
                raise ValueError(f"响应未正常完成：{choice.finish_reason}")
            if not result.strip():
                raise ValueError("响应为空")
            result = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.strip(), flags=re.I)
            value = validator(json.loads(result), compact)
            detail["status"] = "valid"
            request["result"] = value
            return value
        except ValueError as exc:
            feedback = f"\n上次输出未通过校验：{exc}。只重新生成当前部分，不得编造事实或返回截断的 JSON。"
            if isinstance(exc, TruncatedResponse):
                compact = True
                output_tokens = min(settings.CONFIG["max_output_tokens"], output_tokens * 2)
                feedback += "请按新的较短输出要求生成，减少事件数量和文字长度。"
            detail.update(status="invalid", error=str(exc))
            print(f"AI stage={request.get('category') or request['stage']} attempt={attempt + 1}, "
                  f"max_tokens={detail['max_tokens']}, usage={detail.get('usage')}, error={exc}", flush=True)
        except Exception as exc:
            detail.update(status="api_error", error_type=type(exc).__name__)
            raise
        finally:
            detail["elapsed_seconds"] = round(time.monotonic() - started, 3)
            if checkpoint:
                checkpoint()
    raise RuntimeError(f"AI {request.get('category') or request['stage']} {settings.CONFIG['generation_attempts']} 次输出未通过校验：{feedback}")


def generate_digest(articles, run=None, checkpoint=None):
    if not articles:
        raise RuntimeError("指定窗口没有抓到任何文章")
    run = run if run is not None else {}
    run.update(strategy="sections-v1", attempts=[], requests=[], system_prompt=SYSTEM_PROMPT)
    client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url="https://openrouter.ai/api/v1",
                    timeout=settings.CONFIG["api_timeout"], max_retries=1)
    sections, previous_titles = [], []
    for category in CATEGORIES:
        candidates = [a for a in articles if a["category"] == category]
        if len(candidates) < settings.MIN_ITEMS_PER_CATEGORY:
            raise RuntimeError(f"栏目素材不足：{category}")
        request = {"stage": "section", "category": category, "attempts": []}
        run["requests"].append(request)
        maximum = max(settings.MIN_ITEMS_PER_CATEGORY, settings.CONFIG["max_items_per_category"])

        view, aliases = model_view(candidates)

        def validate(value, compact, candidates=candidates, category=category, maximum=maximum, aliases=aliases):
            value = resolve_aliases(value, aliases)
            validate_section(value, category, candidates, settings.MIN_ITEMS_PER_CATEGORY,
                             titles={normalized_title(title) for title in previous_titles},
                             maximum=settings.MIN_ITEMS_PER_CATEGORY if compact else maximum,
                             summary_limit=100 if compact else 160)
            return value

        section = request_json(
            client, request, run,
            lambda compact, view=view, category=category, maximum=maximum: build_section_prompt(
                view, category, settings.MIN_ITEMS_PER_CATEGORY if compact else maximum,
                100 if compact else 120, run, previous_titles, compact),
            validate, min(settings.CONFIG["section_output_tokens"], settings.CONFIG["max_output_tokens"]), checkpoint, aliases,
        )
        sections.append(section)
        previous_titles.extend(item["title"] for item in section["items"])
        run["accepted_sections"] = sections
        if checkpoint:
            checkpoint()

    request = {"stage": "overview", "attempts": []}
    run["requests"].append(request)

    def validate_overview(value, compact):
        if not isinstance(value, dict) or set(value) != {"overview"}:
            raise ValueError("概览必须仅包含 overview 字段")
        text(value["overview"], "overview", 100 if compact else 160)
        return value

    overview = request_json(client, request, run,
        lambda compact: build_overview_prompt(sections) + ("\n此次概览缩短到最多 100 字符。" if compact else ""),
        validate_overview, min(settings.CONFIG["overview_output_tokens"], settings.CONFIG["max_output_tokens"]), checkpoint)
    editorial = validate_editorial({"overview": overview["overview"], "sections": sections}, articles, settings.MIN_ITEMS_PER_CATEGORY)
    run["editorial"] = editorial
    return editorial
