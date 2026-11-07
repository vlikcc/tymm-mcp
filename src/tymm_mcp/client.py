"""tymm.meb.gov.tr için HTTP istemcisi ve HTML ayrıştırıcıları.

Site ASP.NET MVC ile sunucu tarafında üretiliyor. Listeler sayfadaki
jQuery kodunun çağırdığı JSON uç noktalarından geliyor; detay sayfaları
(ders, sınıf, ünite, kitap) ise HTML olarak ayrıştırılıyor.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

BASE_URL = "https://tymm.meb.gov.tr"
USER_AGENT = "tymm-mcp/0.1 (+https://tymm.meb.gov.tr; MCP sunucusu)"
CACHE_TTL = 60 * 60  # 1 saat
MAX_CONCURRENCY = 4


class TymmError(Exception):
    """Siteden veri alınamadığında ya da beklenmeyen yanıt geldiğinde."""


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

_TR_ASCII = str.maketrans("çğıöşüâîûÇĞIİÖŞÜÂÎÛ", "cgiosuaiuCGIIOSUAIU")


def normalize(text: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir (arama için)."""
    text = text.translate(_TR_ASCII)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


def matches(query: str | None, *fields: str | None) -> bool:
    """Sorgudaki her kelime alanlardan birinde geçiyorsa True."""
    if not query:
        return True
    hay = normalize(" ".join(f for f in fields if f))
    return all(word in hay for word in normalize(query).split())


def abs_url(path: str | None) -> str | None:
    if not path:
        return None
    return urljoin(BASE_URL + "/", path)


def html_to_text(node: Tag | None) -> str:
    """HTML parçasını sade Markdown benzeri metne çevirir."""
    if node is None:
        return ""
    out: list[str] = []

    def walk(el: Tag | NavigableString) -> None:
        if isinstance(el, NavigableString):
            out.append(str(el))
            return
        name = el.name
        if name in ("script", "style", "svg", "button"):
            return
        if name == "br":
            out.append("\n")
            return
        if name in ("strong", "b"):
            inner = el.get_text(" ", strip=True)
            if inner:
                out.append(f"**{inner}**")
            return
        if name == "li":
            out.append("\n- ")
            for c in el.children:
                walk(c)
            return
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(name[1])
            out.append("\n\n" + "#" * min(level + 1, 6) + " " + el.get_text(" ", strip=True) + "\n\n")
            return
        if name == "tr":
            cells = [c.get_text(" ", strip=True) for c in el.find_all(["td", "th"])]
            out.append("\n| " + " | ".join(cells) + " |")
            return
        if name == "a" and el.get("href") and not el.get("href", "").startswith(("#", "javascript")):
            text = el.get_text(" ", strip=True)
            href = abs_url(el["href"])
            out.append(f"[{text}]({href})" if text else href or "")
            return
        block = name in ("p", "div", "section", "article", "ul", "ol", "table", "header", "blockquote")
        if block:
            out.append("\n")
        for c in el.children:
            walk(c)
        if block:
            out.append("\n")

    walk(node)
    text = "".join(out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _main(soup: BeautifulSoup) -> Tag:
    main = soup.find("main")
    if not isinstance(main, Tag):
        raise TymmError("Sayfada <main> içeriği bulunamadı.")
    return main


# --------------------------------------------------------------------------
# Veri sınıfları
# --------------------------------------------------------------------------


@dataclass
class Kademe:
    id: int
    ad: str
    slug: str | None
    siniflar: list[dict[str, Any]] = field(default_factory=list)  # {"id", "ad"}


# --------------------------------------------------------------------------
# İstemci
# --------------------------------------------------------------------------


class TymmClient:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- düşük seviye -----------------------------------------------------

    async def _cached(self, key: str, factory):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_TTL:
                return hit[1]
            value = await factory()
            self._cache[key] = (time.monotonic(), value)
            return value

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        async with self._sem:
            for attempt in range(3):
                try:
                    r = await self._http.get(path, params=params)
                except httpx.TransportError as e:
                    if attempt == 2:
                        raise TymmError(f"Siteye bağlanılamadı ({path}): {e}") from e
                    await asyncio.sleep(1 + attempt)
                    continue
                if r.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue
                if r.status_code == 404:
                    raise TymmError(f"Sayfa bulunamadı: {path}")
                if r.status_code >= 500:
                    # Site olmayan kayıtlar için de 500 döndürüyor.
                    raise TymmError(
                        f"{path} için sunucu hatası (HTTP {r.status_code}). Kimlik/slug yanlış olabilir; "
                        "listeleme araçlarıyla doğrulayın."
                    )
                if r.status_code >= 400:
                    raise TymmError(f"{path} için HTTP {r.status_code} döndü.")
                return r
        raise TymmError(f"{path} alınamadı.")

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = "json:" + path + "?" + json.dumps(params or {}, sort_keys=True)

        async def load():
            r = await self._get(path, params)
            try:
                return r.json()
            except ValueError as e:
                raise TymmError(f"{path} JSON döndürmedi.") from e

        return await self._cached(key, load)

    async def get_soup(self, path: str) -> BeautifulSoup:
        async def load():
            r = await self._get(path)
            return r.text

        html = await self._cached("html:" + path, load)
        return BeautifulSoup(html, "html.parser")

    async def _all_pages(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Sayfalı listeyi (20'şerli) baştan sona çeker; aynı seed ile tutarlı sıra."""
        params = dict(params or {})
        first = await self.get_json(path, {**params, "page": 1})
        items: list[dict] = list(first.get("items") or [])
        total_pages = int(first.get("totalPages") or 1)
        seed = first.get("seed")
        if total_pages > 1:
            extra = {"seed": seed} if seed is not None else {}
            pages = await asyncio.gather(
                *(self.get_json(path, {**params, **extra, "page": p}) for p in range(2, total_pages + 1))
            )
            for pg in pages:
                items.extend(pg.get("items") or [])
        seen: set = set()
        unique = []
        for it in items:
            if it.get("id") in seen:
                continue
            seen.add(it.get("id"))
            unique.append(it)
        return unique

    # ---- kademe / sınıf ---------------------------------------------------

    async def kademeler(self) -> list[Kademe]:
        async def load():
            r = await self._get("/ogretim-programlari")
            html = r.text
            soup = BeautifulSoup(html, "html.parser")
            sel = soup.find("select", id="kademe")
            names = {}
            if isinstance(sel, Tag):
                for opt in sel.find_all("option"):
                    v = opt.get("value")
                    if v and v != "0":
                        names[int(v)] = opt.get_text(strip=True)
            m = re.search(r"const sinifMap = (\{.*?\});", html, re.S)
            s = re.search(r"const kademeSlugs = (\{.*?\});", html, re.S)
            sinif_map = json.loads(m.group(1)) if m else {}
            slugs = json.loads(s.group(1)) if s else {}
            result = []
            for kid, ad in names.items():
                siniflar = [{"id": x["id"], "ad": x["seviye"]} for x in sinif_map.get(str(kid), [])]
                result.append(Kademe(id=kid, ad=ad, slug=slugs.get(str(kid)), siniflar=siniflar))
            if not result:
                raise TymmError("Kademe listesi ayrıştırılamadı; site yapısı değişmiş olabilir.")
            return result

        return await self._cached("kademeler", load)

    async def kademe_adi(self, kademe_id: int | None) -> str | None:
        if kademe_id is None:
            return None
        for k in await self.kademeler():
            if k.id == kademe_id:
                return k.ad
        return str(kademe_id)

    async def sinif_adi(self, sinif_id: int) -> str:
        for k in await self.kademeler():
            for s in k.siniflar:
                if s["id"] == sinif_id:
                    return s["ad"]
        return str(sinif_id)

    async def resolve_kademe(self, value: str | int | None) -> Kademe | None:
        """Kademe kimliği, slug'ı ya da adı ile kademe bulur."""
        if value in (None, ""):
            return None
        ks = await self.kademeler()
        sval = str(value)
        for k in ks:
            if sval == str(k.id) or sval == k.slug or normalize(sval) == normalize(k.ad):
                return k
        for k in ks:
            if normalize(sval) in normalize(k.ad):
                return k
        raise TymmError(
            f"'{value}' kademesi bulunamadı. Geçerli kademeler: "
            + ", ".join(f"{k.id}={k.ad}" for k in ks)
        )

    # ---- dersler / programlar ---------------------------------------------

    async def tum_programlar(self, kademe_slug: str | None = None) -> list[dict]:
        params = {"kademe": kademe_slug} if kademe_slug else {}
        return await self._all_pages("/Ders/GetProgramList", params)

    async def sinif_dersleri(self, sinif_id: int, kademe_id: int, sadece_programli: bool = True) -> list[dict]:
        params: dict[str, Any] = {"sinifId": sinif_id, "kademe": kademe_id}
        if sadece_programli:
            params["programListedOnly"] = "true"
        return await self.get_json("/Ders/GetDerslerBySinif", params)

    async def ders_sayfasi(self, ders_slug: str) -> dict:
        soup = await self.get_soup(f"/ogretim-programlari/ders/{ders_slug}")
        main = _main(soup)
        title = main.find(class_="sinif-page-header__title")
        pdf = main.find("a", class_="sinif-page-header__pdf")
        siniflar = []
        for a in main.select(".sinif-grid a.sinif-card__link"):
            href = a.get("href", "")
            m = re.search(r"/ogretim-programlari/([^/]+)/(\d+)$", href)
            siniflar.append(
                {
                    "ad": a.get_text(" ", strip=True),
                    "sinif_id": int(m.group(2)) if m else None,
                    "url": abs_url(href),
                }
            )
        kitaplar = []
        for a in main.select(".sinif-books__card a"):
            href = a.get("href", "")
            m = re.search(r"/kitap/(\d+)/", href)
            sinif = a.find(class_="sinif-books__sinif")
            kitaplar.append(
                {
                    "kitap_id": int(m.group(1)) if m else None,
                    "ad": " ".join((a.get("title") or a.get_text(" ", strip=True)).split()),
                    "sinif": sinif.get_text(strip=True) if sinif else None,
                    "url": abs_url(href),
                }
            )
        return {
            "ders_slug": ders_slug,
            "ad": title.get_text(" ", strip=True) if title else ders_slug,
            "program_pdf": abs_url(pdf.get("href")) if pdf else None,
            "siniflar": siniflar,
            "kitaplar": kitaplar,
            "url": f"{self.base_url}/ogretim-programlari/ders/{ders_slug}",
        }

    async def uniteler(self, ders_slug: str, sinif_id: int) -> dict:
        soup = await self.get_soup(f"/ogretim-programlari/{ders_slug}/{sinif_id}")
        main = _main(soup)
        title = main.find(class_="unite-page-header__title")
        uniteler = []
        # Bazı derslerde üniteler düzey başlıkları altında gruplanmış olabilir.
        current_group = None
        for el in main.find_all(True):
            classes = el.get("class") or []
            if el.name in ("h2", "h3", "h5") and not any(c.startswith("unite-list-item") for c in classes):
                txt = el.get_text(" ", strip=True)
                if txt and "unite-page-header__title" not in classes:
                    current_group = txt
            if el.name == "a" and "unite-list-item__link" in classes:
                href = el.get("href", "")
                m = re.search(r"/unite/(\d+)", href)
                t = el.find(class_="unite-list-item__title")
                uniteler.append(
                    {
                        "unite_id": int(m.group(1)) if m else None,
                        "baslik": (t or el).get_text(" ", strip=True),
                        "grup": current_group,
                        "url": abs_url(href),
                    }
                )
        return {
            "baslik": title.get_text(" ", strip=True) if title else f"{ders_slug} / {sinif_id}",
            "uniteler": uniteler,
            "url": f"{self.base_url}/ogretim-programlari/{ders_slug}/{sinif_id}",
        }

    async def unite_detay(self, ders_slug: str, unite_id: int) -> dict:
        path = f"/{ders_slug}/unite/{unite_id}"
        soup = await self.get_soup(path)
        main = _main(soup)
        title = main.find(class_="unite-detail__title")
        meta = [m.get_text(" ", strip=True) for m in main.select(".unite-detail__meta-item")]
        intro = main.find(class_="unite-detail__intro")
        bolumler: list[dict[str, str]] = []
        for row in main.select(".unite-detail__fields .row"):
            t = row.find(class_="title")
            c = row.find(class_="content")
            if not t or not c:
                continue
            bolumler.append({"baslik": t.get_text(" ", strip=True), "icerik": html_to_text(c)})
        if not title and not bolumler:
            raise TymmError(f"Ünite bulunamadı ya da ayrıştırılamadı: {path}")
        return {
            "baslik": title.get_text(" ", strip=True) if title else f"Ünite {unite_id}",
            "ders": meta[0] if meta else None,
            "sinif": meta[1] if len(meta) > 1 else None,
            "giris": html_to_text(intro) if intro else "",
            "bolumler": bolumler,
            "url": self.base_url + path,
        }

    # ---- kitaplar ---------------------------------------------------------

    async def tum_kitaplar(self) -> list[dict]:
        return await self._all_pages("/Kitap/GetDersKitaplariList")

    async def kitap_detay(self, kitap_id: int) -> dict:
        # Slug zorunlu değil: site /kitap/{id}/{slug} biçimini kullanıyor, ama
        # slug'ı kitap listesinden bulmak en güvenlisi.
        path = None
        for k in await self.tum_kitaplar():
            if k.get("id") == kitap_id:
                path = k.get("detailPath")
                break
        if not path:
            path = f"/kitap/{kitap_id}/x"
        soup = await self.get_soup(path)
        main = _main(soup)
        title = main.find(class_="book-detail-title")
        if not title:
            raise TymmError(f"{kitap_id} numaralı kitap bulunamadı.")
        meta = [m.get_text(" ", strip=True) for m in main.select(".book-meta .meta-badge")]
        updated = main.find(class_="pdf-updated-at")
        viewer = main.find(id="kitap-pdf-viewer")
        pdf = viewer.get("data-pdf") if viewer else None
        if not pdf:
            a = main.find("a", class_="btn-open-new")
            pdf = a.get("href") if a else None
        etk = main.find("a", class_="btn-etkilesimli-kitap")
        upd = updated.get_text(" ", strip=True) if updated else None
        if upd:
            upd = upd.replace("Güncelleme tarihi:", "").strip()
        return {
            "kitap_id": kitap_id,
            "ad": title.get_text(" ", strip=True),
            "etiketler": meta,
            "guncelleme_tarihi": upd,
            "pdf": abs_url(pdf),
            "etkilesimli_kitap": abs_url(etk.get("href")) if etk else None,
            "url": self.base_url + path,
        }

    # ---- genel sayfa ------------------------------------------------------

    async def sayfa(self, yol: str) -> dict:
        parsed = urlparse(yol)
        if parsed.scheme or parsed.netloc:
            if parsed.netloc not in ("tymm.meb.gov.tr", "www.tymm.meb.gov.tr"):
                raise TymmError("Yalnızca tymm.meb.gov.tr sayfaları okunabilir.")
            yol = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if not yol.startswith("/"):
            yol = "/" + yol
        if yol.lower().startswith("/admin"):
            raise TymmError("Bu yol okunamaz.")
        if re.search(r"\.(pdf|pptx?|docx?|xlsx?|zip|mp4)$", urlparse(yol).path, re.I):
            raise TymmError("Bu bir dosya bağlantısı; içerik yerine doğrudan URL'yi kullanın: " + abs_url(yol))
        soup = await self.get_soup(yol)
        main = _main(soup)
        h1 = main.find("h1")
        return {
            "baslik": h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else yol),
            "metin": html_to_text(main),
            "url": self.base_url + yol,
        }
