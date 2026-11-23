"""Read Freesound creators' pack listings (pages robots.txt allows), slowly, and keep packs whose licence is CC0."""
import json, re, sys, time, urllib.parse, urllib.request, urllib.error, html
UA = "freesound-cc0 list (+https://github.com/zuujis-cc0)"

def page(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return None
    finally:
        time.sleep(5)

def parse(t, user):
    out = []
    for m in re.finditer(r'/people/[^/]+/packs/(\d+)/downloaders/', t):
        pid = m.group(1)
        before = t[max(0, m.start() - 6000):m.start()]
        after = t[m.end():m.end() + 600]
        name = re.findall(r'pack_flt=%22' + pid + r'_([^"&]+?)%22', before)
        sounds = re.findall(r'title="([\d,]+) sounds?"', before)
        dls = re.findall(r'title="([\d,]+) downloads?"', before[-800:])
        lic = re.findall(r'title="License: ([^"]+)"', after)
        date = re.findall(r'<span class="h-spacing-left-1 text-light-grey">([^<]+)</span>', before)
        tags = re.findall(r'href="/browse/tags/([^/]+)/\?pack_flt=%22' + pid, before)
        out.append({"user": user, "id": int(pid), "name": html.unescape(urllib.parse.unquote(name[-1])) if name else "?",
                    "sounds": int(sounds[-1].replace(",", "")) if sounds else 0,
                    "downloads": int(dls[-1].replace(",", "")) if dls else 0,
                    "license": lic[0] if lic else "?", "date": date[-1] if date else "", "tags": tags})
    return out

if __name__ == "__main__":
    users, maxpages = sys.argv[1].split(","), int(sys.argv[2])
    res = json.load(open("packs.json")) if len(sys.argv) > 3 else {}
    for u in users:
        got = []
        for p in range(1, maxpages + 1):
            t = page(f"https://freesound.org/people/{u}/packs/?page={p}")
            if not t:
                print(u, "not found" if p == 1 else "", file=sys.stderr); break
            items = parse(t, u)
            got += items
            if len(items) < 15:
                break
        res[u] = got
        cc0 = [x for x in got if x["license"] == "Creative Commons 0"]
        print(f"{u}: {len(got)} packs, {len(cc0)} CC0", file=sys.stderr)
        json.dump(res, open("packs.json", "w"), indent=1)
