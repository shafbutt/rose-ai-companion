"""
ROSE Research Mode + Agent Tools
Research: multi-source web search with summarization.
Agent Tools: quick calculations, notes, reminders.
"""
import re
import time
import json
import os
import threading
import logging

from web_search import search_web

logger = logging.getLogger("rose.research")


# ================================================================== #
#  RESEARCH MODE
# ================================================================== #

def research_topic(query: str, permission_checker=None) -> str:
    """
    Research a topic by searching multiple sources and summarizing findings.
    Returns a spoken-friendly summary string.
    """
    if permission_checker:
        if not permission_checker("web_search"):
            return "I don't have web search permission for research. Please enable it in Settings."
        if not permission_checker("internet"):
            return "I don't have internet permission for research."

    # Perform the search
    result = search_web(query, max_results=8)

    if "error" in result:
        return f"I couldn't complete the research on '{query}'. {result['error']}"

    summary = result.get("summary", "")
    results = result.get("results", [])

    if not summary and not results:
        return f"I searched for '{query}' but couldn't find reliable information."

    # Build a research summary
    parts = []
    if summary:
        parts.append(f"Here's what I found about {query}: {summary}")
    elif results:
        # Combine top results into a coherent summary
        texts = [r["text"] for r in results[:3] if r.get("text")]
        combined = " ".join(texts)
        if len(combined) > 300:
            combined = combined[:300] + "..."
        parts.append(f"Here's what I found about {query}: {combined}")

    # Mention sources
    sources = set()
    for r in results[:5]:
        url = r.get("url", "")
        if url:
            # Extract domain
            try:
                domain = url.split("//")[1].split("/")[0].replace("www.", "")
                sources.add(domain)
            except Exception:
                pass

    if sources:
        parts.append(f"Sources include {', '.join(list(sources)[:3])}.")

    return " ".join(parts)


RESEARCH_PATTERNS = [
    r".*\b(research|deep\s*dive|investigate|find\s*out\s*about|tell\s*me\s*about|explain)\b.*",
    r".*\b(what\s*can\s*you\s*tell\s*me\s*about|what\s*do\s*you\s*know\s*about)\b.*",
]


def check_research_query(text: str) -> bool:
    """Check if user wants research mode."""
    lower = text.lower()
    return any(re.match(pat, lower) for pat in RESEARCH_PATTERNS)


def handle_research_query(text: str, permission_checker=None) -> str:
    """Handle research queries. Returns reply or None."""
    if not check_research_query(text):
        return None
    # Extract topic
    topic = re.sub(r"^(research|deep dive into|investigate|find out about|tell me about|explain)\s*", "", text, flags=re.I)
    topic = topic.rstrip("?").rstrip(".").strip()
    if not topic:
        return "What topic would you like me to research?"
    return research_topic(topic, permission_checker)


# ================================================================== #
#  AGENT TOOLS: Calculations, Notes, Quick Facts
# ================================================================== #

def handle_calculation(text: str) -> str:
    """Handle simple math calculations. Returns reply or None."""
    # Match patterns like "what is 5 + 3", "calculate 100 / 4", "what's 15 * 3"
    calc_pattern = r"(?:what(?:'s| is)\s*|calculate\s*|compute\s*|solve\s*)?(\d[\d\s\+\-\*\/\.\(\)]+[\d\)])\s*\??$"
    m = re.search(calc_pattern, text.lower().strip())
    if not m:
        return None

    expr = m.group(1).strip()
    # Safety: only allow numbers, operators, parens, dots, spaces
    if not re.match(r'^[\d\s\+\-\*\/\.\(\)]+$', expr):
        return None

    try:
        # Evaluate safely (only math operations)
        result = eval(expr, {"__builtins__": {}}, {})
        if isinstance(result, (int, float)):
            # Format nicely
            if isinstance(result, float) and result == int(result):
                result = int(result)
            return f"The answer is {result}."
    except Exception:
        return None
    return None


# ---- Notes (stored in memory) ----
NOTES_PATTERNS = [
    r".*\b(take\s*(a\s*)?note|write\s*(this\s*)?down|note\s*that|save\s*this)\b.*",
    r".*\b(add\s*(a\s*)?note|create\s*(a\s*)?note|jot\s*(this\s*)?down)\b.*",
]


def check_note_request(text: str) -> bool:
    lower = text.lower()
    return any(re.match(pat, lower) for pat in NOTES_PATTERNS)


# ---- Quick unit conversions ----
CONVERSION_PATTERN = r"(?:convert\s*)?(\d+\.?\d*)\s*(km|mi|m|ft|cm|in|kg|lb|g|oz|c|f|l|gal)\s*(?:to|in)\s*(km|mi|m|ft|cm|in|kg|lb|g|oz|c|f|l|gal)"

CONVERSIONS = {
    ("km", "mi"): 0.621371, ("mi", "km"): 1.60934,
    ("m", "ft"): 3.28084, ("ft", "m"): 0.3048,
    ("cm", "in"): 0.393701, ("in", "cm"): 2.54,
    ("kg", "lb"): 2.20462, ("lb", "kg"): 0.453592,
    ("g", "oz"): 0.035274, ("oz", "g"): 28.3495,
    ("l", "gal"): 0.264172, ("gal", "l"): 3.78541,
}


def handle_conversion(text: str) -> str:
    """Handle unit conversions. Returns reply or None."""
    m = re.search(CONVERSION_PATTERN, text.lower())
    if not m:
        return None
    try:
        value = float(m.group(1))
        from_unit = m.group(2)
        to_unit = m.group(3)
        # Temperature special case
        if from_unit == "c" and to_unit == "f":
            result = value * 9/5 + 32
            return f"{value}°C is {round(result, 1)}°F."
        if from_unit == "f" and to_unit == "c":
            result = (value - 32) * 5/9
            return f"{value}°F is {round(result, 1)}°C."
        # Standard conversion
        key = (from_unit, to_unit)
        if key in CONVERSIONS:
            result = value * CONVERSIONS[key]
            return f"{value} {from_unit} is {round(result, 4)} {to_unit}."
        if from_unit == to_unit:
            return f"{value} {from_unit} is {value} {to_unit} (same unit)."
    except Exception:
        pass
    return None


# ---- Combined agent handler ----
def handle_agent_tools(text: str, memory_manager=None) -> str:
    """
    Handle agent tool requests (calculations, conversions, notes).
    Returns reply string or None.
    """
    # Calculations
    calc_reply = handle_calculation(text)
    if calc_reply:
        return calc_reply

    # Conversions
    conv_reply = handle_conversion(text)
    if conv_reply:
        return conv_reply

    # Notes (store in memory)
    if check_note_request(text) and memory_manager:
        # Extract the note content
        note = re.sub(r"^(take\s*(a\s*)?note|write\s*(this\s*)?down|note\s*that|save\s*this|add\s*(a\s*)?note|create\s*(a\s*)?note|jot\s*(this\s*)?down)\s*[:\-\s]*", "", text, flags=re.I).strip()
        if note:
            memory_manager.store(note, category="note", importance=5)
            return f"Got it, I've noted that down."
        return "What should I note down?"

    return None
