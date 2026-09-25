import html
import re
import urllib.request

URLS = {
    "ccl_readme": "https://raw.githubusercontent.com/GJSeason/CCL2023-FCC/main/README.md",
    "ieee_rules": "https://www.kaggle.com/competitions/ieee-fraud-detection/rules",
}

HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

for name, url in URLS.items():
    req = urllib.request.Request(url, headers=HDR)
    try:
        raw = urllib.request.urlopen(req, timeout=60).read()
    except Exception as exc:
        print(name, "ERROR", repr(exc))
        continue
    with open(f"work/{name}.html", "wb") as f:
        f.write(raw)
    text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"(?is)<script.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = "\n".join(lines)
    with open(f"work/{name}.txt", "w", encoding="utf-8") as f:
        f.write(out)
    print(name, len(raw), len(out))
