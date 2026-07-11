import os

from google import genai
from google.genai import types

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

CHAT_MODEL = "gemini-flash-latest"

SYSTEM_PROMPT = """
You are Velora, a high-end AI engineering assistant.

You analyze repositories and answer technical questions using ONLY the provided repository context.

Instructions:
- Be precise
- Be concise but useful
- Do not hallucinate missing implementation details
- If the context is insufficient, explicitly say it
- Mention file paths when relevant
- Think like a senior engineer explaining a codebase to another engineer
"""


def generate_answer(question: str, context: str):
    try:
        user_prompt = f"""
Repository context:
{context}

Question:
{question}

Please return:

Answer:
<your answer>

Relevant files:
- <path>
- <path>

Explanation:
<short explanation>
"""

        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
            ),
        )

        return response.text

    except Exception as e:
        return f"Error generating answer: {str(e)}"
