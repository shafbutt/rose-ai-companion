"""
ROSE Web Search + YouTube Search Module
Provides web search (DuckDuckGo) and YouTube search capabilities.

Web Search: Uses DuckDuckGo Instant Answer API (free, no key required).
YouTube: Opens search results in default browser (requires browser permission).

Both features require permission checks before use.
"""
import json
import urllib.request
import urllib.parse
import urllib.error
import webbrowser
import logging
import re

logger = logging.getLogger("rose.search")


def search_web(query: str, max_results: int = 5) -> dict:
    """
    Search the web using DuckDuckGo Instant Answer API.
    Returns dict with 'results' list and 'summary' string.
    """
    try:
        encoded = urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
        url = f"https://api.duckduckgo.com/?{encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "ROSE-Assistant/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        results = []
        summary = ""

        # Abstract (main summary)
        if data.get("Abstract"):
            summary = data["Abstract"]
            if data.get("AbstractSource"):
                summary += f" (Source: {data['AbstractSource']})"

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "text": topic["Text"],
                    "url": topic.get("FirstURL", ""),
                })

        # Results section
        for r in data.get("Results", [])[:max_results]:
            if isinstance(r, dict) and r.get("Text"):
                results.append({
                    "text": r["Text"],
                    "url": r.get("FirstURL", ""),
                })

        return {
            "query": query,
            "summary": summary,
            "results": results,
            "source": "DuckDuckGo",
        }

    except Exception as e:
        logger.warning(f"Web search failed: {e}")
        return {"query": query, "summary": "", "results": [], "error": str(e)}


def search_youtube(query: str) -> dict:
    """
    Search YouTube and return the top results.
    Uses YouTube's search URL to get video IDs.
    Returns dict with 'videos' list.
    """
    try:
        encoded = urllib.parse.urlencode({"search_query": query})
        url = f"https://www.youtube.com/results?{encoded}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Extract video IDs from the HTML
        video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        # Remove duplicates while preserving order
        seen = set()
        unique_ids = []
        for vid in video_ids:
            if vid not in seen:
                seen.add(vid)
                unique_ids.append(vid)

        videos = []
        for vid in unique_ids[:5]:
            videos.append({
                "id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": f"YouTube Video ({vid})",  # Title extraction requires more parsing
            })

        return {"query": query, "videos": videos, "source": "YouTube"}

    except Exception as e:
        logger.warning(f"YouTube search failed: {e}")
        return {"query": query, "videos": [], "error": str(e)}


def open_youtube_search(query: str) -> str:
    """Open YouTube search in the default browser."""
    try:
        encoded = urllib.parse.urlencode({"search_query": query})
        url = f"https://www.youtube.com/results?{encoded}"
        webbrowser.open(url)
        return f"Opening YouTube search for '{query}'."
    except Exception as e:
        return f"Could not open YouTube: {e}"


def open_web_search(query: str) -> str:
    """Open a web search in the default browser."""
    try:
        encoded = urllib.parse.urlencode({"q": query})
        url = f"https://duckduckgo.com/?{encoded}"
        webbrowser.open(url)
        return f"Opening web search for '{query}'."
    except Exception as e:
        return f"Could not open browser: {e}"


def format_search_reply(result: dict) -> str:
    """Format web search results into a friendly spoken response."""
    if "error" in result:
        return f"I couldn't search the web right now. {result['error']}"

    summary = result.get("summary", "")
    results = result.get("results", [])

    if summary:
        # Truncate for speech
        if len(summary) > 200:
            summary = summary[:200] + "..."
        return f"Here's what I found: {summary}"
    elif results:
        first = results[0].get("text", "")
        if len(first) > 200:
            first = first[:200] + "..."
        return f"Here's what I found: {first}"
    else:
        return f"I searched for '{result.get('query', '')}' but didn't find any clear results."


def format_youtube_reply(result: dict) -> str:
    """Format YouTube search results into a friendly response."""
    if "error" in result:
        return f"I couldn't search YouTube right now. {result['error']}"

    videos = result.get("videos", [])
    if not videos:
        return f"I couldn't find any YouTube videos for '{result.get('query', '')}'."

    count = len(videos)
    return f"I found {count} video{'s' if count != 1 else ''} on YouTube. Opening the search in your browser."


# ---- Voice command patterns ----
WEB_SEARCH_PATTERNS = [
    r".*\b(search\s*(the\s*web|google|online)|look\s*up|find\s*(info|information)|what\s*is)\b.*",
    r".*\b(who\s*is|what\s*are|where\s*is|when\s*did|how\s*does)\b.*\b\?$",
]

YOUTUBE_PATTERNS = [
    r".*\b(search\s*youtube|youtube\s*search|find\s*(a\s*)?video|look\s*up.*on\s*youtube)\b.*",
    r".*\b(play|show)\b.*\b(youtube|video)\b.*",
]


def check_web_search_query(text: str) -> bool:
    """Check if user is asking for a web search."""
    lower = text.lower().strip()
    # Direct search commands
    if any(re.match(pat, lower) for pat in WEB_SEARCH_PATTERNS):
        return True
    # Questions that need current info
    if lower.startswith(("who is", "what is", "where is", "when did", "how does", "what are")):
        if "?" in lower or lower.startswith(("who is", "what is")):
            return True
    return False


def check_youtube_query(text: str) -> bool:
    """Check if user is asking for YouTube search."""
    lower = text.lower()
    return any(re.match(pat, lower) for pat in YOUTUBE_PATTERNS)


def handle_web_search_query(text: str, permission_checker=None) -> str:
    """
    Handle web search queries. Returns reply string or None if not a search query.
    """
    # Check YouTube first (more specific pattern)
    if check_youtube_query(text):
        if permission_checker and not permission_checker("browser"):
            return "I don't have browser permission. Please enable it in Settings → Permissions to search YouTube."
        # Extract search query
        query = _extract_search_query(text, youtube=True)
        if not query:
            return "What would you like me to search for on YouTube?"
        result = search_youtube(query)
        open_youtube_search(query)
        return format_youtube_reply(result)

    # Web search
    if check_web_search_query(text):
        if permission_checker and not permission_checker("web_search"):
            return "I don't have web search permission. Please enable it in Settings → Permissions."
        if permission_checker and not permission_checker("internet"):
            return "I don't have internet permission. I need it to search the web."
        query = _extract_search_query(text, youtube=False)
        if not query:
            return "What would you like me to search for?"
        result = search_web(query)
        return format_search_reply(result)

    return None


def _extract_search_query(text: str, youtube: bool = False) -> str:
    """Extract the actual search query from the user's sentence."""
    lower = text.lower().strip()
    # Remove common prefixes
    prefixes = [
        "search youtube for", "search the web for", "search google for",
        "search online for", "look up", "find information about",
        "find info on", "find", "what is", "who is", "where is",
        "when did", "how does", "what are",
    ]
    query = text.strip()
    for prefix in prefixes:
        if lower.startswith(prefix):
            query = text[len(prefix):].strip()
            break
    # Remove trailing question marks
    query = query.rstrip("?").rstrip(".").strip()
    return query if query else ""
