#!/usr/bin/env python3
"""library.py: licence-checked music for motion-mode videos, kept as one shared library.

    python3 library.py list                                   what is in the library, with licence and tempo
    python3 library.py add <freemusicarchive track page URL> [--id name] [--tags "calm,minimal"]
    python3 library.py seed                                   (re)build the vetted set in catalog.json
    python3 library.py check [--id name]                      re-fetch each page: is the licence unchanged?
    python3 library.py show <id>                              one track's record (credit, analysis, files)

The library lives in $MOTION_MUSIC or ~/.lab-video/music/<id>/ (audio.mp3, page.html, track.json). Audio stays
out of the skill; catalog.json in this folder is the vetted list that rebuilds it on any machine.

The licence gate reads the licence from the track's OWN page (FMA's rel="license" block), never from the
artist, the album or a search listing: on Broke For Free's "Something EP" only "Something Elated" is CC BY,
and the other four tracks are NonCommercial. Allowed: CC BY (any version or port; credit required) and CC0
or the Public Domain Mark. Refused: NonCommercial (a sales video is commercial), NoDerivatives (cutting a bed
is a derivative), ShareAlike (could pull the whole video under the same licence), and anything unreadable.
"""
import argparse
import datetime
import html
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import media as mt  # noqa: E402
import analyze  # noqa: E402
import project as pj  # noqa: E402

CATALOG = HERE / "catalog.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126 Safari/537.36 lab-video-music-library"}


def library_dir():
    return pj.music_dir()


class Refused(Exception):
    """A track the library will not take, with the reason a person can act on."""


def fetch(url, binary=False, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return r.status, r.geturl(), r.headers.get("content-type", ""), data if binary else data.decode("utf-8", "replace")


def short_name(licence_url):
    """https://creativecommons.org/licenses/by/3.0/us/ -> ("by", "CC BY 3.0 US")."""
    m = re.search(r"creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)/([0-9.]+)/?([a-z]{2})?/?$", licence_url or "")
    if not m:
        return None, None
    family, kind, version, port = m.groups()
    if family == "publicdomain":
        return ("zero", "CC0 %s" % version) if kind == "zero" else ("mark", "Public Domain Mark %s" % version) if kind == "mark" else (kind, kind)
    label = "CC " + kind.upper().replace("-", "-") + " " + version + (" " + port.upper() if port else "")
    return kind, label


def parse_page(page):
    """The track page's own licence block and player metadata."""
    links = re.findall(r'<a rel="license"[^>]*href="([^"]+)"', page)
    distinct = sorted(set(u.rstrip("/") + "/" for u in links))
    name = re.search(r'<a rel="license" href="[^"]+"[^>]*>([^<]+)</a>', page)
    title = re.search(r'property="dc:title"[^>]*>([^<]+)<', page)
    who = re.search(r'property="cc:attributionName">\s*([^<]+)</a>', page)
    infos = re.findall(r"data-track-info='([^']*)'", page)
    info = json.loads(html.unescape(infos[0])) if infos else {}
    genres = sorted(set(re.findall(r'href="https://freemusicarchive\.org/genre/([^/"]+)/?"', page)))
    return {"licenceUrls": distinct, "licenceName": html.unescape(name[1]).strip() if name else None,
            "title": html.unescape(title[1]).strip() if title else info.get("title"),
            "artist": html.unescape(who[1]).strip() if who else info.get("artistName"),
            "album": info.get("albumTitle"), "fileUrl": info.get("fileUrl"), "page": info.get("url"),
            "genres": [g.replace("_", " ") for g in genres]}


def licence_verdict(meta):
    """(kind, short) for an allowed licence, else Refused with the reason."""
    urls = meta["licenceUrls"]
    if not urls:
        raise Refused("the track page shows no licence; without one the track is all rights reserved")
    if len(urls) > 1:
        raise Refused("the track page names %d different licences (%s); ask the artist" % (len(urls), ", ".join(urls)))
    kind, short = short_name(urls[0])
    if kind is None:
        raise Refused("unrecognised licence %s" % urls[0])
    if kind in ("zero", "mark", "by"):
        return kind, short
    parts = set(kind.split("-"))
    why = []
    if "nc" in parts:
        why.append("NonCommercial rules out a sales or customer video")
    if "nd" in parts:
        why.append("NoDerivatives rules out cutting, fading or mixing it into a film")
    if "sa" in parts:
        why.append("ShareAlike could require the whole video to carry the same licence")
    raise Refused("%s: %s" % (short, "; ".join(why) or "not an allowed licence"))


def credit_lines(rec):
    """On-screen line, and the full attribution (title, author, source, licence, changes) for the file."""
    t, a, lic = rec["title"], rec["artist"], rec["licence"]
    if lic["kind"] in ("zero", "mark"):
        screen = 'Music: "%s" by %s' % (t, a)
    else:
        screen = 'Music: "%s" by %s · %s' % (t, a, lic["short"])
    full = ('"%s" by %s, from Free Music Archive (%s), licensed under %s (%s).'
            % (t, a, rec["page"], lic["short"], lic["url"]))
    return screen, full


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:40]


def add(url, track_id=None, tags=None, quiet=False, refresh=False):
    status, final, _, page = fetch(url)
    if status != 200:
        raise Refused("the page answered %s" % status)
    meta = parse_page(page)
    kind, short = licence_verdict(meta)
    if not meta.get("fileUrl"):
        raise Refused("the page has no audio file link")
    tid = track_id or slug(meta["title"])
    folder = library_dir() / tid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "page.html").write_text(page)
    audio = folder / "audio.mp3"
    known = folder / "track.json"
    if not refresh and known.exists() and audio.exists():
        rec = json.loads(known.read_text())
        if rec.get("files", {}).get("audioSha256") == mt.sha256(audio) and rec["licence"]["short"] == short:
            # The licence was just re-read on the track's own page; the audio is the same file. Nothing to fetch.
            rec["licence"]["checked"] = datetime.date.today().isoformat()
            rec["files"]["pageSha256"] = mt.sha256(folder / "page.html")
            if tags:
                rec["tags"] = tags
            mt.write_json(known, rec)
            if not quiet:
                print("kept      %-18s licence re-checked: %s" % (tid, short))
            return rec
    status, _, ctype, data = fetch(meta["fileUrl"], binary=True, timeout=300)
    if status != 200 or len(data) < 200_000 or not ("audio" in ctype or meta["fileUrl"].endswith(".mp3")):
        raise Refused("the audio download failed (%s, %s, %d bytes)" % (status, ctype, len(data)))
    tmp = audio.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, audio)
    report = analyze.analyze(audio)
    rec = {"id": tid, "title": meta["title"], "artist": meta["artist"], "album": meta["album"],
           "page": meta["page"] or final, "fileUrl": meta["fileUrl"], "genres": meta["genres"], "tags": tags or [],
           "licence": {"kind": kind, "short": short, "url": meta["licenceUrls"][0], "name": meta["licenceName"],
                       "checked": datetime.date.today().isoformat(), "evidence": "page.html (the track page as fetched)",
                       "creditRequired": kind == "by"},
           "files": {"audio": "audio.mp3", "audioSha256": mt.sha256(audio), "page": "page.html", "pageSha256": mt.sha256(folder / "page.html")},
           "analysis": report}
    rec["credit"], rec["attribution"] = credit_lines(rec)
    mt.write_json(folder / "track.json", rec)
    if not quiet:
        print("added     %-18s %s, %s · %.1f BPM · %s" % (tid, rec["title"], rec["artist"], report["tempo"]["bpm"],
              "final hit at %.2f s" % report["finalHit"]["seconds"] if report["finalHit"] else "no final hit (fades)"))
    return rec


def load(track_id):
    f = library_dir() / track_id / "track.json"
    if not f.exists():
        known = ", ".join(sorted(p.name for p in library_dir().glob("*/track.json"))) if library_dir().exists() else ""
        raise SystemExit("no track %r in %s%s" % (track_id, library_dir(), " (have: %s)" % known if known else "; run library.py seed"))
    rec = json.loads(f.read_text())
    rec["folder"] = str(f.parent)
    rec["audioPath"] = str(f.parent / rec["files"]["audio"])
    return rec


def tracks():
    out = []
    for f in sorted(library_dir().glob("*/track.json")):
        try:
            rec = json.loads(f.read_text())
            rec["folder"] = str(f.parent)
            out.append(rec)
        except ValueError:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("add")
    p.add_argument("url")
    p.add_argument("--id")
    p.add_argument("--tags", default="")
    sd = sub.add_parser("seed")
    sd.add_argument("--refresh", action="store_true", help="download every track again")
    c = sub.add_parser("check")
    c.add_argument("--id")
    s = sub.add_parser("show")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "add":
        try:
            add(a.url, a.id, [t.strip() for t in a.tags.split(",") if t.strip()])
        except Refused as r:
            sys.exit("REFUSED   %s: %s" % (a.url, r))
    elif a.cmd == "seed":
        cat = json.loads(CATALOG.read_text())
        bad = 0
        for t in cat["tracks"]:
            try:
                add(t["page"], t["id"], t.get("tags", []), refresh=a.refresh)
            except Refused as r:
                bad += 1
                print("REFUSED   %-18s %s" % (t["id"], r))
            time.sleep(0.5)
        for t in cat.get("refusalExamples", []):
            try:
                st, _, _, page = fetch(t["page"])
                licence_verdict(parse_page(page))
                bad += 1
                print("UNEXPECTED %s was accepted; catalog.json says it must be refused" % t["page"])
            except Refused as r:
                print("refused   %-18s (as expected) %s" % (t["id"], r))
        sys.exit(1 if bad else 0)
    elif a.cmd == "list":
        rows = tracks()
        if not rows:
            print("library %s is empty: python3 library.py seed" % library_dir())
        for r in rows:
            an = r["analysis"]
            print("%-18s %-34s %-12s %6.1f BPM  %6.1f s  %s  %s" % (
                r["id"], ('"%s", %s' % (r["title"], r["artist"]))[:34], r["licence"]["short"], an["tempo"]["bpm"],
                an["decodedSeconds"], ("hit %.1f s" % an["finalHit"]["seconds"]) if an["finalHit"] else "fades   ",
                ",".join(r.get("tags") or [])))
    elif a.cmd == "check":
        bad = 0
        for r in tracks():
            if a.id and r["id"] != a.id:
                continue
            try:
                st, _, _, page = fetch(r["page"])
                kind, short = licence_verdict(parse_page(page))
                same = short == r["licence"]["short"]
                bad += 0 if same else 1
                print(("ok        " if same else "CHANGED   ") + "%-18s %s (recorded %s, checked %s)" % (r["id"], short, r["licence"]["short"], r["licence"]["checked"]))
            except Refused as e:
                bad += 1
                print("REFUSED   %-18s now: %s" % (r["id"], e))
            except Exception as e:  # offline: say so, do not pretend it passed
                print("UNKNOWN   %-18s could not fetch the page (%s)" % (r["id"], e))
        sys.exit(1 if bad else 0)
    elif a.cmd == "show":
        r = load(a.id)
        if a.json:
            print(json.dumps(r, indent=2))
        else:
            an = r["analysis"]
            print('%s: "%s" by %s (%s)\n  licence  %s  %s  checked %s\n  page     %s\n  credit   %s\n  tempo    %.2f BPM, bar0 %.3f s (%s)\n  ending   %s\n  level    %s LUFS, true peak %s dBTP\n  file     %s'
                  % (r["id"], r["title"], r["artist"], r["album"], r["licence"]["short"], r["licence"]["url"], r["licence"]["checked"],
                     r["page"], r["credit"], an["tempo"]["bpm"], an["tempo"]["bar0Seconds"], an["tempo"]["downbeatConfidence"],
                     ("final hit %.3f s (bar %s), rings to %.2f s" % (an["finalHit"]["seconds"], an["finalHitBar"], an["ringEnd"])) if an["finalHit"] else "no final hit; fades, rings to %.2f s" % an["ringEnd"],
                     an["loudness"]["integrated_lufs"], an["loudness"]["true_peak_dbtp"], r["audioPath"]))


if __name__ == "__main__":
    main()
