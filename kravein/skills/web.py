"""Web search, page fetching and weather.

Ports ``actions/web_search.py``, ``actions/weather_report.py`` and the URL
half of ``actions/browser_control.py``. These were already cross-platform;
the Windows-specific part was locating the default browser through the
registry, which is now the freedesktop ``xdg-settings`` mechanism.
"""

from __future__ import annotations

import json
import urllib.parse

import requests

from ..platform import apps, shell
from ..registry import skill
from ..safety import Danger

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Kravein/2.0"
TIMEOUT = 20


@skill(category="web", danger=Danger.SAFE)
def web_search(query: str, max_results: int = 6) -> str:
    """Search the web and return result titles, snippets and links.

    Args:
        query: What to search for.
        max_results: How many results to return.
    """
    query = (query or "").strip()
    if not query:
        return "What should I search for?"

    max_results = max(1, min(int(max_results), 20))

    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore[no-redef]
        except ImportError:
            return "Web search needs the 'ddgs' package. Run: pip install ddgs"

    try:
        with DDGS() as engine:
            results = list(engine.text(query, max_results=max_results))
    except Exception as exc:
        return f"Search failed: {exc}"

    if not results:
        return f"No results for '{query}'."

    lines = [f"{len(results)} result(s) for '{query}':"]
    for i, item in enumerate(results, 1):
        title = item.get("title", "").strip()
        body = " ".join((item.get("body") or "").split())[:220]
        href = item.get("href", "")
        lines.append(f"\n{i}. {title}\n   {body}\n   {href}")
    return "\n".join(lines)


@skill(category="web", danger=Danger.SAFE)
def fetch_page(url: str, max_chars: int = 4000) -> str:
    """Fetch a web page and return its readable text.

    Args:
        url: The page to fetch.
        max_chars: Maximum characters of text to return.
    """
    url = (url or "").strip()
    if not url:
        return "Give a URL to fetch."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Could not fetch {url}: {exc}"

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return f"{url} returned {content_type}, not readable text."

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        text = response.text
    else:
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text("\n")

    lines = [l.strip() for l in text.splitlines()]
    cleaned = "\n".join(l for l in lines if l)

    limit = max(500, min(int(max_chars), 20000))
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + f"\n… [{len(cleaned) - limit} more chars]"

    return f"{url}\n\n{cleaned}"


@skill(category="web", danger=Danger.MODERATE)
def open_url(url: str, browser: str = "") -> str:
    """Open a URL in the browser.

    Args:
        url: Where to go.
        browser: A specific browser command; omit for your default.
    """
    url = (url or "").strip()
    if not url:
        return "Give a URL to open."
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url

    if browser:
        binary = shell.which(browser)
        if not binary:
            matches = apps.find(browser, limit=1)
            if matches:
                shell.spawn(matches[0].command() + [url])
                return f"Opened {url} in {matches[0].name}."
            return f"Browser '{browser}' not found."
        shell.spawn([binary, url])
        return f"Opened {url} in {browser}."

    return apps.open_uri(url)


@skill(category="web", danger=Danger.SAFE)
def search_and_open(query: str) -> str:
    """Search the web and open the top result in the browser.

    Args:
        query: What to search for.
    """
    encoded = urllib.parse.quote_plus(query)
    return open_url(f"https://duckduckgo.com/?q={encoded}")


@skill(category="web", danger=Danger.SAFE)
def default_browser() -> str:
    """Report which browser is set as the system default.

    Replaces reading the Windows registry ProgId.
    """
    if shell.have("xdg-settings"):
        result = shell.run(["xdg-settings", "get", "default-web-browser"], timeout=8)
        if result.ok and result.stdout.strip():
            entry_id = result.stdout.strip()
            for entry in apps.index():
                if entry.id == entry_id:
                    return f"Default browser: {entry.name} ({entry_id})"
            return f"Default browser: {entry_id}"

    if shell.have("xdg-mime"):
        result = shell.run(["xdg-mime", "query", "default", "x-scheme-handler/https"], timeout=8)
        if result.ok and result.stdout.strip():
            return f"Default browser: {result.stdout.strip()}"

    return "Could not determine the default browser."


@skill(category="web", danger=Danger.SAFE)
def weather(city: str = "") -> str:
    """Get the current weather and a short forecast.

    Args:
        city: City name. Omit to use your approximate location.
    """
    location = urllib.parse.quote(city.strip()) if city.strip() else ""

    # wttr.in needs no API key, which keeps this working out of the box.
    try:
        response = requests.get(
            f"https://wttr.in/{location}?format=j1",
            headers={"User-Agent": "curl/8"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        return f"Could not fetch the weather: {exc}"

    try:
        current = data["current_condition"][0]
        area = data["nearest_area"][0]
        place = ", ".join(filter(None, [
            (area.get("areaName") or [{}])[0].get("value", ""),
            (area.get("country") or [{}])[0].get("value", ""),
        ]))

        lines = [
            f"Weather for {place or city or 'your location'}:",
            f"  {(current.get('weatherDesc') or [{}])[0].get('value', '?')}",
            f"  Temperature : {current.get('temp_C')}°C "
            f"(feels like {current.get('FeelsLikeC')}°C)",
            f"  Humidity    : {current.get('humidity')}%",
            f"  Wind        : {current.get('windspeedKmph')} km/h",
        ]

        for day in data.get("weather", [])[:3]:
            date = day.get("date", "")
            lines.append(
                f"  {date}: {day.get('mintempC')}–{day.get('maxtempC')}°C"
            )
        return "\n".join(lines)
    except (KeyError, IndexError, TypeError) as exc:
        return f"Weather data was in an unexpected format: {exc}"
