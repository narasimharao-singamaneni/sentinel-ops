import os
import re

from openai import OpenAI
import json
from openwindow.engine.data_models import ExecutionData
from openwindow.config.settings import LLM_API, LLM_MODEL

LLM_API_KEY = os.getenv("GROQ_API_KEY")

client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_API
)


def generate_explanation(previous, priority, current, park, date):
    prompt = f"""
    You are an assistant that explains park availability changes.

    Previous: {previous}
    Current: {current}
    Priority: {priority}
    Park: {park}
    Date: {date}

    Return ONLY valid JSON:

    {{
    "reason": "short explanation",
    "urgency": "HIGH or LOW",
    "action": "what user should do"
    }}

    Rules:
    - Do NOT include any extra text
    - Do NOT include <think>
    - Keep it concise
    Priority meaning:
    - HIGH → urgency must be HIGH
    - LOW → urgency must be LOW
    """

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.5
    )

    response_text = response.choices[0].message.content
    cleaned = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    
    if match:
        json_text = match.group(0)
    else:
        json_text = cleaned

    try:
        parsed = json.loads(json_text)
    except Exception:
        parsed = {
            "reason": cleaned,
            "urgency": "UNKNOWN",
            "action": "Check manually"
        }
    return parsed