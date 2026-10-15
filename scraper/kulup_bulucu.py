#!/usr/bin/env python3
"""
UniPakt Kulüp Bulucu
====================

Üniversitelerin resmi sitelerini gezip öğrenci kulübü adlarını, Instagram
hesaplarını ve e-posta adreslerini toplar; sonucu CSV ve JSON olarak yazar.

Kurallar:
  * robots.txt'ye uyar. Bir site otomatik erişimi yasaklıyorsa o sayfayı atlar
    ve sana sayfayı tarayıcıdan kaydetmeni söyler (bkz. README).
  * Her istek arasında bekler (varsayılan 2 sn), siteleri yormaz.
  * Instagram'a hiç istek atmaz; sadece üniversite sayfalarında geçen
    Instagram linklerini okur.

Kullanım:
    python3 kulup_bulucu.py                       # config'teki tüm üniversiteler
    python3 kulup_bulucu.py --sadece Yeditepe     # adında "Yeditepe" geçenler
    python3 kulup_bulucu.py --derinlik 2 --max-sayfa 40
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Eksik paket var. Önce şunu çalıştır:  pip3 install requests beautifulsoup4")


# ---------------------------------------------------------------------------
# Ayarlar
# ---------------------------------------------------------------------------

USER_AGENT = "UniPaktKulupBulucu/0.1 (ogrenci projesi)"
TIMEOUT = 15  # saniye

# Bir metnin kulüp adı olabileceğini gösteren kelimeler
CLUB_NAME_RE = re.compile(
    r"(kul[üu]b|kul[üu]p|toplulu[ğg]|club|society|ieee|acm|gdg|gdsc|aiesec|esn)",
    re.I,
)
# Kulüp adı gibi görünen ama aslında genel başlık / belge olan metinler
GENERIC_RE = re.compile(
    r"^(t[üu]m |[öo][ğg]renci )?(kul[üu]pler(i|imiz)?|topluluklar(ı|imiz)?|student clubs?)$"
    r"|y[öo]nerge|y[öo]netmeli|t[üu]z[üu]k|ba[şs]vuru|form(u)?\b|kurulu[şs]|nas[ıi]l"
    r"|kul[üu]p kur|duyuru|haber|etkinlik takvimi|\.pdf",
    re.I,
)
# Etkinlik, kategori başlığı, kural cümlesi gibi kulüp olmayan satırlar
NOISE_RE = re.compile(
    r"kul[üu]pler(i|imiz)?\b|\bclubs\b|topluluklar|"
    r"etkinli[ğg]i|event|workshop|screening|sempozyum|welcome|quiz|night|activities|"
    r"tan[ıi][şs]ma|tak[ıi]m[ıi]m[ıi]z|proje|project|kul[üu]p ba[şs]kan|[üu]yeli[ğg]i|ilkeler|"
    r"dan[ıi][şs]man|koordinat[öo]r|sorumlu|advisor|"
    r"\b20\d\d\b|\s[xX]\s",
    re.I,
)
# Gezinirken takip edilecek linkleri seçen kelimeler (URL'de veya link metninde)
NAV_LINK_RE = re.compile(
    r"(kul[üu]p|kul[üu]b|kulup|toplulu|topluluk|club|society|sks|"
    r"ogrenci-?yasam|[öo][ğg]renci ya[şs]am|student-?life|kampus|kamp[üu]s ya[şs]am)",
    re.I,
)
INSTAGRAM_URL_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})", re.I)
INSTAGRAM_AT_RE = re.compile(r"(?:instagram|insta|ig)\s*[:\-–]?\s*@([A-Za-z0-9_.]{2,30})", re.I)
INSTAGRAM_RESERVED = {
    "p", "reel", "reels", "explore", "accounts", "stories", "tv", "about",
    "developer", "legal", "direct", "share", "web",
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".doc", ".docx",
            ".xls", ".xlsx", ".ppt", ".pptx", ".mp4", ".webp")
# Kulüp adının arkasına yapışmış iletişim bilgisini kesmek için
CONTACT_TAIL_RE = re.compile(r"\s(?:instagram|insta|ig|e-?posta|e-?mail|mail|tel|@|https?://)", re.I)
CONTAINER_TAGS = {"li", "tr", "article", "div", "section", "td"}
NAME_TAGS = ["h1", "h2", "h3", "h4", "h5", "a", "strong", "b", "td", "li", "span", "p"]


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------

@dataclass
class Club:
    universite: str
    kulup: str
    instagram: set[str] = field(default_factory=set)
    eposta: set[str] = field(default_factory=set)
    kaynak: str = ""
    guven: str = "orta"  # "yüksek": detay sayfasından ya da iletişimiyle birlikte bulundu

    def as_row(self) -> dict:
        return {
            "universite": self.universite,
            "kulup": self.kulup,
            "instagram": " ".join(sorted(self.instagram)),
            "eposta": " ".join(sorted(self.eposta)),
            "guven": self.guven,
            "kaynak": self.kaynak,
        }


# ---------------------------------------------------------------------------
# İnternetten sayfa çekme (robots.txt'ye uyarak)
# ---------------------------------------------------------------------------

class Fetcher:
    def __init__(self, delay: float):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._last_request = 0.0

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=TIMEOUT)
                rp.parse(r.text.splitlines() if r.status_code < 400 else [])
            except requests.RequestException:
                rp.parse([])  # robots.txt okunamazsa: kısıt yok kabul et
            self._robots[base] = rp
        return self._robots[base].can_fetch(USER_AGENT, url)

    def get(self, url: str) -> tuple[str | None, str | None]:
        """(html, hata) döndürür. Hata varsa html None olur."""
        if not self.allowed(url):
            return None, "robots.txt izin vermiyor"
        wait = self.delay - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()
        try:
            r = self.session.get(url, timeout=TIMEOUT)
        except requests.RequestException as e:
            return None, f"bağlantı hatası: {e.__class__.__name__}"
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}"
        if "html" not in r.headers.get("Content-Type", "html"):
            return None, "HTML değil"
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, None


# ---------------------------------------------------------------------------
# Sayfadan bilgi çıkarma
# ---------------------------------------------------------------------------

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" -–•|:\u00a0")


def norm_key(name: str) -> str:
    """Aynı kulübün farklı yazımlarını eşleştirmek için sadeleştirilmiş anahtar."""
    tr = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")
    s = re.sub(r"\(.*?\)|\s[-–]\s.*$", "", name).translate(tr).lower()  # "(YUCOMP)", " - YEU"
    s = re.sub(r"\b(kulubu|kulup|toplulugu|club|society)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def find_contacts(node) -> tuple[set[str], set[str]]:
    """Bir HTML parçasındaki Instagram hesaplarını ve e-postaları bulur."""
    raw = str(node)
    text = node.get_text(" ")
    igs = {h.rstrip(".").lower() for h in INSTAGRAM_URL_RE.findall(raw)}
    igs |= {h.rstrip(".").lower() for h in INSTAGRAM_AT_RE.findall(text)}
    igs -= INSTAGRAM_RESERVED
    mails = {m.lower() for m in EMAIL_RE.findall(raw)
             if not m.lower().endswith(SKIP_EXT)}
    return igs, mails


def strip_site_chrome(soup: BeautifulSoup) -> None:
    """Menü, üst bilgi ve alt bilgiyi siler; üniversitenin kendi Instagram'ı
    her sayfada footer'da durduğu için kulüplere karışmasın."""
    for tag in soup.find_all(["header", "footer", "nav", "script", "style", "noscript"]):
        tag.decompose()
    for tag in soup.select('[class*="footer"], [id*="footer"], [class*="menu"], [id*="menu"]'):
        tag.decompose()


def smallest_container(el):
    """İsim elementinin, iletişim bilgisini de içerebilecek en yakın kutusu."""
    node = el
    for _ in range(4):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "[document]"):
            break
        node = parent
        if node.name in CONTAINER_TAGS and len(node.get_text(" ")) > len(el.get_text(" ")) + 3:
            if len(node.get_text(" ")) > 700:  # liste kutusunun tamamına çıktık, dur
                return el
            return node
    return el


def looks_like_club(text: str) -> bool:
    if "@" in text or "http" in text.lower() or "www." in text.lower():
        return False  # bu bir hesap adı / e-posta / link, kulüp adı değil
    if " " not in text and text == text.lower():
        return False  # "yeditepetipkulubu" gibi tek kelimelik Instagram kullanıcı adı
    if re.match(r"^\d", text) or any(ch in text for ch in "?:;") or text.endswith("."):
        return False  # tarih, soru, cümle
    if len(text.split()) > 10:
        return False  # paragraf cümlesi
    return (4 <= len(text) <= 90 and bool(CLUB_NAME_RE.search(text))
            and not GENERIC_RE.search(text) and not NOISE_RE.search(text))


def is_english_page(url: str) -> bool:
    """/en/ altındaki sayfalar Türkçe sayfaların tekrarı; atla."""
    return "/en/" in urlparse(url).path.lower() + "/" or urlparse(url).path.lower().startswith("/en")


def extract_clubs(html: str, url: str, universite: str) -> list[Club]:
    soup = BeautifulSoup(html, "html.parser")
    strip_site_chrome(soup)
    found: dict[str, Club] = {}

    # 1) Kulüp detay sayfası mı? (başlık kulüp adıysa sayfadaki iletişimler ona aittir)
    h1 = soup.find("h1")
    if h1 and looks_like_club(clean(h1.get_text(" "))):
        igs, mails = find_contacts(soup)
        if len(igs) <= 2:  # 2'den fazlaysa liste sayfasıdır, detay değil
            name = clean(h1.get_text(" "))
            found[norm_key(name)] = Club(universite, name, igs, mails, url,
                                         "yüksek" if (igs or mails) else "orta")

    # 2) Liste sayfası: her kulüp adını ve yakınındaki iletişimi topla
    for el in soup.find_all(NAME_TAGS):
        # İçinde kulüp adı taşıyan daha küçük bir element varsa onu kullan
        # (yoksa "Dans Kulübü Instagram: @x" gibi satırın tamamı ad sanılır)
        if any(looks_like_club(clean(c.get_text(" "))) for c in el.find_all(NAME_TAGS)):
            continue
        name = clean(CONTACT_TAIL_RE.split(el.get_text(" "))[0])
        if not looks_like_club(name):
            continue
        key = norm_key(name)
        if not key or key in found:
            continue
        igs, mails = find_contacts(smallest_container(el))
        found[key] = Club(universite, name, igs, mails, url,
                          "yüksek" if (igs or mails) else "orta")

    return list(found.values())


def candidate_links(html: str, page_url: str, domain: str) -> list[str]:
    """Gezilecek alt sayfalar: aynı sitede, kulüp/topluluk ile ilgili linkler."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = urldefrag(urljoin(page_url, a["href"]))[0]
        p = urlparse(href)
        if p.scheme not in ("http", "https") or not p.netloc.endswith(domain):
            continue
        if p.path.lower().endswith(SKIP_EXT) or is_english_page(href):
            continue
        hint = href + " " + a.get_text(" ")
        if NAV_LINK_RE.search(hint) or CLUB_NAME_RE.search(a.get_text(" ")):
            links.append(href)
    return links


def registered_domain(url: str) -> str:
    """'www.sks.bilgi.edu.tr' -> 'bilgi.edu.tr' (alt alan adlarını da kapsamak için)."""
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("edu", "com", "org"):   # x.edu.tr
        if parts[-3] == "bilim" and len(parts) >= 4:            # demiroglu.bilim.edu.tr
            return ".".join(parts[-4:])
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host     # sabanciuniv.edu


# ---------------------------------------------------------------------------
# Bir üniversiteyi tara
# ---------------------------------------------------------------------------

def scan_university(uni: dict, fetcher: Fetcher, depth: int, max_pages: int,
                    base_dir: Path) -> tuple[list[Club], list[str]]:
    name = uni["ad"]
    clubs: dict[str, Club] = {}
    notes: list[str] = []

    def merge(new: list[Club]) -> None:
        for c in new:
            k = norm_key(c.kulup)
            if k in clubs:
                old = clubs[k]
                old.instagram |= c.instagram
                old.eposta |= c.eposta
                if len(c.kulup) > len(old.kulup):
                    old.kulup = c.kulup  # "Bilgisayar Topluluğu (YUCOMP)" daha bilgilendirici
                if c.guven == "yüksek":
                    old.guven = "yüksek"
            else:
                clubs[k] = c

    for source in uni["kaynaklar"]:
        # Yerel dosya (tarayıcıdan kaydedilmiş sayfa)
        if not source.startswith("http"):
            path = (base_dir / source)
            if path.exists():
                merge(extract_clubs(path.read_text(encoding="utf-8", errors="ignore"),
                                    f"dosya:{source}", name))
                print(f"   📄 {source} okundu")
            continue

        # İnternetten gez (BFS)
        domain = registered_domain(source)
        queue = deque([(source, 0)])
        seen: set[str] = set()
        while queue and len(seen) < max_pages:
            url, d = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            html, err = fetcher.get(url)
            if err:
                if url == source:
                    notes.append(f"{source} -> {err}")
                    print(f"   ⚠️  {source}: {err}")
                continue
            merge(extract_clubs(html, url, name))
            if d < depth:
                for link in candidate_links(html, url, domain):
                    if link not in seen:
                        queue.append((link, d + 1))
        print(f"   🌐 {source}: {len(seen)} sayfa gezildi")

    return list(clubs.values()), notes


# ---------------------------------------------------------------------------
# Çıktı
# ---------------------------------------------------------------------------

def postprocess(clubs: list[Club]) -> list[Club]:
    """Son temizlik:
      * kulüp olmayan satırları ve İngilizce sayfa tekrarlarını at,
      * aynı kulübün farklı yazımlarını birleştir,
      * üniversitenin kendi Instagram hesabını (3+ kulüpte görünen hesap) sil.
    """
    merged: dict[tuple[str, str], Club] = {}
    for c in clubs:
        if not looks_like_club(c.kulup) or is_english_page(c.kaynak.replace("dosya:", "/")):
            continue
        key = (c.universite, norm_key(c.kulup))
        if key in merged:
            old = merged[key]
            old.instagram |= c.instagram
            old.eposta |= c.eposta
            if len(c.kulup) > len(old.kulup):
                old.kulup = c.kulup
            if c.guven == "yüksek":
                old.guven = "yüksek"
        else:
            merged[key] = c

    result = list(merged.values())
    for uni in {c.universite for c in result}:
        uni_clubs = [c for c in result if c.universite == uni]
        counts: dict[str, int] = {}
        for c in uni_clubs:
            for h in c.instagram:
                counts[h] = counts.get(h, 0) + 1
        shared = {h for h, n in counts.items() if n >= 3}
        for c in uni_clubs:
            c.instagram -= shared
            if len(c.eposta) > 3:      # bir kulübün 3'ten fazla e-postası olmaz: yanlış eşleşme
                c.eposta = set()
            if len(c.instagram) > 2:
                c.instagram = set()
            if not c.instagram and not c.eposta:
                c.guven = "orta"
    return result


def load_previous(json_path: Path) -> list[Club]:
    """Daha önce yazılmış kulupler.json'u Club listesine geri çevirir."""
    rows = json.loads(json_path.read_text(encoding="utf-8"))
    return [Club(r["universite"], r["kulup"], set(r["instagram"].split()),
                 set(r["eposta"].split()), r["kaynak"], r["guven"]) for r in rows]


def upload_to_supabase(clubs: list[Club]) -> None:
    """SUPABASE_URL ve SUPABASE_SERVICE_KEY ortam değişkenleri varsa kulüpleri
    panelin veritabanına yükler (GitHub Action bu yolu kullanır).

    Veritabanındaki kulupleri_yukle() fonksiyonu yeni kulüpleri ekler; var olan
    kulüplerde sadece BOŞ Instagram/e-posta alanlarını doldurur. Durum, kim yazdı
    ve notlara asla dokunmaz.
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):  # eski tip (JWT) anahtar
        headers["Authorization"] = f"Bearer {key}"

    def fit_emails(emails: set[str], limit: int = 300) -> str:
        out = ""
        for e in sorted(emails):
            cand = f"{out} {e}".strip()
            if len(cand) > limit:
                break
            out = cand
        return out

    rows = [{
        "universite": c.universite[:150],
        "kulup": c.kulup[:150],
        "kulup_key": (norm_key(c.kulup) or c.kulup.lower())[:150],
        "instagram": sorted(c.instagram)[0][:200] if c.instagram else "",
        "eposta": fit_emails(c.eposta),
        "kaynak": c.kaynak[:500],
        "guven": c.guven,
    } for c in clubs]

    def post(batch: list[dict]):
        return requests.post(f"{url}/rest/v1/rpc/kulupleri_yukle", headers=headers,
                             json={"satirlar": batch}, timeout=60)

    added = updated = 0
    skipped: list[str] = []
    for i in range(0, len(rows), 200):
        batch = rows[i:i + 200]
        r = post(batch)
        if r.status_code in (401, 403):
            sys.exit(f"Supabase anahtarı reddedildi (HTTP {r.status_code}). SUPABASE_SERVICE_KEY doğru mu?")
        if r.status_code >= 400:
            # Bir satır hatalıysa bütün grup reddedilir: satırları tek tek dene, bozuk olanı atla
            for row in batch:
                r1 = post([row])
                if r1.status_code >= 400:
                    skipped.append(f"{row['universite']} / {row['kulup']}: {r1.text[:120]}")
                    continue
                res = r1.json()[0]
                added += res["eklenen"]
                updated += res["guncellenen"]
            continue
        res = r.json()[0]
        added += res["eklenen"]
        updated += res["guncellenen"]
    for line in skipped:
        print(f"   ⚠️  atlandı: {line}")
    print(f"☁️  Supabase: {added} yeni kulüp eklendi, {updated} kulübün eksik bilgisi dolduruldu"
          f"{f', {len(skipped)} satır atlandı' if skipped else ''}.")


def write_outputs(clubs: list[Club], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [c.as_row() for c in sorted(clubs, key=lambda c: (c.universite, c.kulup))]
    fields = ["universite", "kulup", "instagram", "eposta", "guven", "kaynak"]
    with open(out_dir / "kulupler.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    with open(out_dir / "kulupler.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser(description="UniPakt Kulüp Bulucu")
    ap.add_argument("--config", default="universiteler.json")
    ap.add_argument("--sadece", help="Sadece adında bu metin geçen üniversiteleri tara")
    ap.add_argument("--derinlik", type=int, default=3, help="Link takip derinliği (vars. 3)")
    ap.add_argument("--max-sayfa", type=int, default=80, help="Üniversite başına en fazla sayfa")
    ap.add_argument("--bekleme", type=float, default=2.0, help="İstekler arası saniye")
    ap.add_argument("--cikti", default="cikti")
    ap.add_argument("--temizle", action="store_true",
                    help="Siteleri gezmeden, mevcut cikti/kulupler.json'u yeniden temizle")
    args = ap.parse_args()

    out_dir = Path(args.cikti)
    previous_json = out_dir / "kulupler.json"
    previous = load_previous(previous_json) if previous_json.exists() else []
    notes_by_uni: dict[str, list[str]] = {}

    if args.temizle:
        if not previous:
            sys.exit(f"Temizlenecek dosya yok: {previous_json}")
        all_clubs = previous
    else:
        config_path = Path(args.config)
        unis = json.loads(config_path.read_text(encoding="utf-8"))
        if args.sadece:
            unis = [u for u in unis if args.sadece.lower() in u["ad"].lower()]
        if not unis:
            sys.exit("Taranacak üniversite bulunamadı.")

        fetcher = Fetcher(args.bekleme)
        scanned = {u["ad"] for u in unis}
        # Taranmayan üniversitelerin önceki sonuçlarını koru
        all_clubs = [c for c in previous if c.universite not in scanned]
        for uni in unis:
            print(f"\n🔎 {uni['ad']}")
            clubs, notes = scan_university(uni, fetcher, args.derinlik, args.max_sayfa,
                                           config_path.parent)
            all_clubs.extend(clubs)
            notes_by_uni[uni["ad"]] = notes

    all_clubs = postprocess(all_clubs)
    write_outputs(all_clubs, out_dir)
    upload_to_supabase(all_clubs)

    print("\n" + "=" * 60)
    print(f"{'Üniversite':40} Kulüp  IG  Mail")
    for uni in sorted({c.universite for c in all_clubs} | set(notes_by_uni)):
        clubs = [c for c in all_clubs if c.universite == uni]
        ig = sum(1 for c in clubs if c.instagram)
        mail = sum(1 for c in clubs if c.eposta)
        print(f"{uni[:40]:40} {len(clubs):5} {ig:3} {mail:5}")
        for note in notes_by_uni.get(uni, []):
            print(f"   ⚠️  {note}")
    print(f"\n✅ Sonuç: {out_dir}/kulupler.csv ve kulupler.json")
    print("   'robots.txt izin vermiyor' görürsen README'deki 'Elle kaydetme' adımına bak.")


if __name__ == "__main__":
    main()
