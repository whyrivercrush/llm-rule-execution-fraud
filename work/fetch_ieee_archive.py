import html
import re
import urllib.request

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

cdx = (
    "http://web.archive.org/cdx/search/cdx?url=kaggle.com%2Fc%2Fieee-fraud-detection"
    "%2Frules&output=json&limit=15&filter=statuscode%3A200&collapse=digest"
)
req = urllib.request.Request(cdx, headers=HDR)
rows = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", errors="ignore")
print(rows[:4000])
