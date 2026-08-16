"""
Synthesizes the qualitative research note by grounding Claude in:
  1. the retrieved chunks from annual reports / transcripts (RAG context)
  2. the computed quantitative outputs (ratios, CAPM, risk, red flags)

Uses Claude Haiku by default (cheapest current Anthropic model) since this
is a summarization/synthesis task, not a task needing frontier reasoning -
swap ANTHROPIC_MODEL in .env if you want higher quality at higher cost.
"""
import anthropic
from app.config import settings

SYSTEM_PROMPT = """You are a sell-side equity research analyst writing in the
style of JPMorgan / Nomura research notes: precise, data-grounded, and
neutral in tone. You are given (a) retrieved excerpts from the company's
own annual reports and earnings call transcripts, and (b) pre-computed
quantitative outputs (ratios, CAPM, risk metrics, rule-based red flags).

Rules:
- Only make qualitative claims that are supported by the retrieved excerpts
  provided. If the excerpts don't cover something, say the data wasn't
  available rather than inventing it.
- Reference the quantitative outputs directly (cite the actual numbers).
- Structure the note as: Executive Summary, Financial Performance, Balance
  Sheet & Leverage, Valuation, Risk Factors, Red Flags, and a closing line
  that this is a research aid, not investment advice.
- Keep it under 600 words. Neutral, professional tone - no hype language.
"""


def generate_research_note(
    company_name: str,
    retrieved_chunks: list[str],
    quantitative_summary: str,
) -> str:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    context_block = "\n\n---\n\n".join(retrieved_chunks) if retrieved_chunks else \
        "(No source document excerpts were retrieved for this company - " \
        "base qualitative commentary only on the quantitative outputs below.)"

    user_prompt = f"""Company: {company_name}

Retrieved source excerpts:
{context_block}

Pre-computed quantitative outputs:
{quantitative_summary}

Write the research note now."""

    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
