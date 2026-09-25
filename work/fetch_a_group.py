import html
import re
import urllib.request

URLS = {
    "dk_llm": "https://ar5iv.labs.arxiv.org/html/2506.21443",
    "finfre": "https://ar5iv.labs.arxiv.org/html/2512.13040",
    "unidetect": "https://ar5iv.labs.arxiv.org/html/2604.12329",
}

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

for name, url in URLS.items():
    req = urllib.request.Request(url, headers=HDR)
    raw = urllib.request.urlopen(req, timeout=90).read()
    with open(f"work/{name}.html", "wb") as f:
        f.write(raw)
    text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"(?is)<script.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    out = "\n".join(lines)
    with open(f"work/{name}.txt", "w", encoding="utf-8") as f:
        f.write(out)
    print(name, len(raw), len(out))
