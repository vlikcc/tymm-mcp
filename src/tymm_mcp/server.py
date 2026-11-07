"""Türkiye Yüzyılı Maarif Modeli (tymm.meb.gov.tr) MCP sunucusu."""

from __future__ import annotations

import asyncio
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .client import TymmClient, TymmError, matches, normalize

mcp = MCPServer(
    "tymm",
    instructions=(
        "Türkiye Yüzyılı Maarif Modeli (MEB) sitesindeki öğretim programlarına, ünite/tema "
        "içeriklerine (öğrenme çıktıları, beceriler, ders saatleri) ve ders kitaplarına erişim sağlar. "
        "Tipik akış: ders_ara → ders_programi (sınıf kimlikleri) → uniteleri_listele → unite_detay. "
        "Bir sınıfın tüm öğrenme çıktıları için ogrenme_ciktilari aracını kullanın. "
        "Kitaplar için kitap_ara ve kitap_detay. Diğer sayfalar (beceriler, genel bakış vb.) için "
        "sayfalari_listele ve sayfa_oku. Yanıtlarda kaynak URL'yi belirtin."
    ),
)

RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True, idempotentHint=True)

_client: TymmClient | None = None


def client() -> TymmClient:
    global _client
    if _client is None:
        _client = TymmClient()
    return _client


async def _ders_coz(ders: str) -> dict:
    """Ders slug'ı ya da adından program kaydını bulur."""
    ders = ders.strip().strip("/")
    if "/" in ders:  # tam yol verildiyse son parçayı al
        ders = ders.rsplit("/", 1)[-1]
    programs = await client().tum_programlar()
    for p in programs:
        if p.get("url") == ders:
            return p
    exact = [p for p in programs if normalize(p.get("dersAdi", "")) == normalize(ders)]
    if len(exact) == 1:
        return exact[0]
    found = [p for p in programs if matches(ders, p.get("dersAdi"), p.get("url"))]
    if len(found) == 1:
        return found[0]
    if not found and not exact:
        # Listede olmayan ama sitede var olan bir slug olabilir.
        return {"url": ders, "dersAdi": ders, "id": None, "kademe": None}
    adaylar = exact or found
    kademe_adlari = {k.id: k.ad for k in await client().kademeler()}
    satirlar = "\n".join(
        f"- {p['dersAdi']} ({kademe_adlari.get(p.get('kademe'), p.get('kademe'))}) → `{p['url']}`"
        for p in adaylar[:25]
    )
    raise TymmError(f"'{ders}' birden fazla dersle eşleşti; ders_slug olarak birini seçin:\n{satirlar}")


def _hata(e: Exception) -> str:
    return f"Hata: {e}"


# --------------------------------------------------------------------------
# Kademe ve dersler
# --------------------------------------------------------------------------


@mcp.tool(annotations=RO)
async def kademeleri_listele() -> str:
    """Okul türlerini/kademeleri ve her kademedeki sınıf seviyelerini kimlikleriyle listeler.

    Sınıf kimlikleri (sinif_id) diğer araçlarda kullanılır. Örn. 9.Sınıf = 11, 12.Sınıf = 14.
    """
    try:
        ks = await client().kademeler()
    except TymmError as e:
        return _hata(e)
    lines = ["# Kademeler ve sınıflar", ""]
    for k in ks:
        lines.append(f"## {k.ad} (kademe_id={k.id}, slug={k.slug})")
        lines.append(", ".join(f"{s['ad']} (sinif_id={s['id']})" for s in k.siniflar) or "-")
        lines.append("")
    return "\n".join(lines)


@mcp.tool(annotations=RO)
async def ders_ara(
    sorgu: Annotated[str | None, Field(description="Ders adında aranacak kelime(ler), ör. 'matematik', 'kimya'. Boş bırakılırsa tümü listelenir.")] = None,
    kademe: Annotated[str | None, Field(description="Kademe kimliği, slug'ı ya da adı (ör. '3', 'ortaogretim', 'Temel Eğitim').")] = None,
) -> str:
    """Öğretim programı bulunan dersleri ada ve/veya kademeye göre arar.

    Dönen `ders_slug` değeri ders_programi, uniteleri_listele ve ogrenme_ciktilari araçlarında kullanılır.
    """
    try:
        k = await client().resolve_kademe(kademe)
        programs = await client().tum_programlar(k.slug if k else None)
        kademe_adlari = {x.id: x.ad for x in await client().kademeler()}
    except TymmError as e:
        return _hata(e)
    found = [p for p in programs if matches(sorgu, p.get("dersAdi"), p.get("url"))]
    found.sort(key=lambda p: (p.get("kademe") or 0, normalize(p.get("dersAdi", ""))))
    if not found:
        return f"'{sorgu}' ile eşleşen ders bulunamadı ({len(programs)} ders tarandı)."
    lines = [f"{len(found)} ders bulundu:", "", "| Ders | Kademe | ders_id | ders_slug |", "|---|---|---|---|"]
    for p in found:
        lines.append(
            f"| {p.get('dersAdi')} | {kademe_adlari.get(p.get('kademe'), p.get('kademe'))} | {p.get('id')} | `{p.get('url')}` |"
        )
    return "\n".join(lines)


@mcp.tool(annotations=RO)
async def sinifa_gore_dersler(
    kademe: Annotated[str, Field(description="Kademe kimliği, slug'ı ya da adı (ör. '2' ya da 'temel-egitim').")],
    sinif_id: Annotated[int, Field(description="Sınıf kimliği (kademeleri_listele ile öğrenilir; ör. 5.Sınıf=6, 10.Sınıf=12).")],
) -> str:
    """Belirli bir kademe ve sınıf seviyesinde öğretim programı olan dersleri, program PDF bağlantılarıyla listeler."""
    try:
        k = await client().resolve_kademe(kademe)
        assert k is not None
        dersler = await client().sinif_dersleri(sinif_id, k.id)
        sinif = await client().sinif_adi(sinif_id)
    except TymmError as e:
        return _hata(e)
    if not dersler:
        return f"{k.ad} / {sinif} için ders bulunamadı. Sınıfın bu kademede olduğundan emin olun (kademeleri_listele)."
    lines = [f"# {k.ad} — {sinif} dersleri ({len(dersler)})", "", "| Ders | ders_id | ders_slug | Program PDF |", "|---|---|---|---|"]
    for d in dersler:
        pdf = f"{client().base_url}/assets/pdf/{d['pdf']}" if d.get("pdf") else "-"
        lines.append(f"| {d.get('dersAdi')} | {d.get('id')} | `{d.get('url')}` | {pdf} |")
    return "\n".join(lines)


@mcp.tool(annotations=RO)
async def ders_programi(
    ders: Annotated[str, Field(description="ders_slug (ör. 'kimya-dersi') ya da ders adı.")],
) -> str:
    """Bir dersin öğretim programı sayfasını getirir: program PDF'i, sınıf seviyeleri (sinif_id) ve derse ait kitaplar."""
    try:
        p = await _ders_coz(ders)
        d = await client().ders_sayfasi(p["url"])
    except TymmError as e:
        return _hata(e)
    lines = [f"# {d['ad']}", f"Kaynak: {d['url']}", f"ders_slug: `{d['ders_slug']}`"]
    if p.get("id"):
        lines.append(f"ders_id: {p['id']}")
    lines.append(f"Öğretim programı PDF: {d['program_pdf'] or '-'}")
    lines += ["", "## Sınıf seviyeleri"]
    for s in d["siniflar"]:
        lines.append(f"- {s['ad']} (sinif_id={s['sinif_id']})")
    if d["kitaplar"]:
        lines += ["", "## Kitaplar"]
        for b in d["kitaplar"]:
            lines.append(f"- [{b['sinif'] or ''}] {b['ad']} (kitap_id={b['kitap_id']})")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Üniteler / temalar
# --------------------------------------------------------------------------


@mcp.tool(annotations=RO)
async def uniteleri_listele(
    ders: Annotated[str, Field(description="ders_slug (ör. 'kimya-dersi') ya da ders adı.")],
    sinif_id: Annotated[int, Field(description="Sınıf kimliği (ders_programi aracı dersin sınıflarını verir).")],
) -> str:
    """Bir dersin belirli sınıf seviyesindeki ünite/temalarını unite_id değerleriyle listeler."""
    try:
        p = await _ders_coz(ders)
        u = await client().uniteler(p["url"], sinif_id)
    except TymmError as e:
        return _hata(e)
    if not u["uniteler"]:
        return f"{u['baslik']} için ünite bulunamadı. Kaynak: {u['url']}"
    lines = [f"# {u['baslik']}", f"Kaynak: {u['url']}", ""]
    grup = object()
    for x in u["uniteler"]:
        if x["grup"] != grup and x["grup"]:
            lines.append(f"\n## {x['grup']}")
        grup = x["grup"]
        lines.append(f"- {x['baslik']} (unite_id={x['unite_id']})")
    lines.append(f"\nAyrıntı için: unite_detay(ders='{p['url']}', unite_id=...)")
    return "\n".join(lines)


@mcp.tool(annotations=RO)
async def unite_detay(
    ders: Annotated[str, Field(description="ders_slug (ör. 'kimya-dersi') ya da ders adı.")],
    unite_id: Annotated[int, Field(description="Ünite/tema kimliği (uniteleri_listele ile öğrenilir).")],
    bolumler: Annotated[
        list[str] | None,
        Field(
            description=(
                "İsteğe bağlı: yalnızca bu bölümleri getir (başlıkta geçen kelimeyle eşleşir). "
                "Örn. ['Öğrenme Çıktıları', 'Ders Saati', 'Anahtar Kavramlar', 'İçerik Çerçevesi', "
                "'Alan Becerileri', 'Öğrenme Kanıtları', 'Öğrenme-Öğretme Uygulamaları', 'Farklılaştırma']. "
                "Boş bırakılırsa tüm bölümler gelir."
            )
        ),
    ] = None,
) -> str:
    """Bir ünite/temanın tüm program içeriğini getirir.

    İçerik: ders saati, alan/kavramsal beceriler, eğilimler, programlar arası bileşenler (SDB, değerler,
    okuryazarlık), öğrenme çıktıları ve süreç bileşenleri, içerik çerçevesi, anahtar kavramlar,
    ölçme-değerlendirme, öğrenme-öğretme uygulamaları, zenginleştirme ve destekleme.
    """
    try:
        p = await _ders_coz(ders)
        u = await client().unite_detay(p["url"], unite_id)
    except TymmError as e:
        return _hata(e)
    secili = u["bolumler"]
    if bolumler:
        secili = [b for b in secili if any(matches(q, b["baslik"]) for q in bolumler)]
    lines = [f"# {u['baslik']}", f"{u['ders'] or ''} — {u['sinif'] or ''}".strip(" —"), f"Kaynak: {u['url']}", ""]
    if u["giris"] and not bolumler:
        lines += [u["giris"], ""]
    for b in secili:
        lines += [f"## {b['baslik']}", b["icerik"] or "-", ""]
    if bolumler and not secili:
        lines.append("İstenen bölümler bulunamadı. Mevcut bölümler: " + ", ".join(b["baslik"] for b in u["bolumler"]))
    return "\n".join(lines)


@mcp.tool(annotations=RO)
async def ogrenme_ciktilari(
    ders: Annotated[str, Field(description="ders_slug (ör. 'matematik-dersi') ya da ders adı.")],
    sinif_id: Annotated[int, Field(description="Sınıf kimliği (ders_programi aracı dersin sınıflarını verir).")],
) -> str:
    """Bir dersin bir sınıf seviyesindeki TÜM ünitelerinin öğrenme çıktılarını ve ders saatlerini tek seferde getirir.

    Yıllık plan, kazanım listesi ya da ölçme aracı hazırlarken kullanışlıdır.
    """
    try:
        p = await _ders_coz(ders)
        u = await client().uniteler(p["url"], sinif_id)
        detaylar = await asyncio.gather(
            *(client().unite_detay(p["url"], x["unite_id"]) for x in u["uniteler"] if x["unite_id"]),
            return_exceptions=True,
        )
    except TymmError as e:
        return _hata(e)
    if not u["uniteler"]:
        return f"{u['baslik']} için ünite bulunamadı. Kaynak: {u['url']}"
    lines = [f"# {u['baslik']} — Öğrenme çıktıları", f"Kaynak: {u['url']}", ""]
    toplam = 0
    for x, d in zip([x for x in u["uniteler"] if x["unite_id"]], detaylar):
        if isinstance(d, Exception):
            lines += [f"## {x['baslik']}", f"(alınamadı: {d})", ""]
            continue
        saat = next((b["icerik"] for b in d["bolumler"] if matches("ders saati", b["baslik"])), None)
        cikti = next((b["icerik"] for b in d["bolumler"] if matches("ogrenme ciktilari", b["baslik"])), None)
        try:
            toplam += int(str(saat).split()[0])
        except (ValueError, IndexError, TypeError):
            pass
        baslik = f"## {d['baslik']} (unite_id={x['unite_id']})"
        if saat:
            baslik += f" — {saat} ders saati"
        lines += [baslik, cikti or "-", ""]
    if toplam:
        lines.append(f"**Toplam ders saati:** {toplam}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Kitaplar
# --------------------------------------------------------------------------


@mcp.tool(annotations=RO)
async def kitap_ara(
    sorgu: Annotated[str | None, Field(description="Kitap adında aranacak kelimeler, ör. 'fizik 10', 'öğretmen kılavuz', 'çalışma kitabı'.")] = None,
    kademe: Annotated[str | None, Field(description="Kademe kimliği, slug'ı ya da adı.")] = None,
    sinif_id: Annotated[int | None, Field(description="Sınıf kimliği (ör. 9.Sınıf=11).")] = None,
    ders_id: Annotated[int | None, Field(description="Ders kimliği (ders_ara/sinifa_gore_dersler çıktısındaki ders_id).")] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="En fazla kaç sonuç döneceği.")] = 40,
) -> str:
    """Ders kitapları, öğretmen kılavuz kitapları, çalışma kitapları ve etkinlik kitapları arasında arama yapar.

    Her sonuç için PDF ve (varsa) etkileşimli kitap bağlantısı döner.
    """
    try:
        k = await client().resolve_kademe(kademe)
        kitaplar = await client().tum_kitaplar()
    except TymmError as e:
        return _hata(e)
    found = [
        b
        for b in kitaplar
        if matches(sorgu, b.get("title"))
        and (k is None or b.get("kademe") == k.id)
        and (sinif_id is None or sinif_id in (b.get("sinifIds") or []))
        and (ders_id is None or b.get("dersId") == ders_id)
    ]
    found.sort(key=lambda b: normalize(b.get("title", "")))
    if not found:
        return f"Eşleşen kitap bulunamadı ({len(kitaplar)} kitap tarandı)."
    base = client().base_url
    lines = [f"{len(found)} kitap bulundu" + (f" (ilk {limit} gösteriliyor)" if len(found) > limit else "") + ":", ""]
    for b in found[:limit]:
        pdf = b.get("pdfUrl")
        pdf = (base + pdf) if pdf and pdf.startswith("/") else pdf
        etk = b.get("etkilesimliKitapUrl")
        line = f"- **{' '.join(str(b.get('title')).split())}** (kitap_id={b.get('id')}, ders_id={b.get('dersId')})\n  PDF: {pdf or '-'}"
        if etk:
            line += f" · Etkileşimli: {base + etk if etk.startswith('/') else etk}"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool(annotations=RO)
async def kitap_detay(
    kitap_id: Annotated[int, Field(description="Kitap kimliği (kitap_ara ya da ders_programi çıktısından).")],
) -> str:
    """Bir kitabın ayrıntılarını getirir: sınıf/ders/kademe etiketleri, güncelleme tarihi, PDF ve etkileşimli kitap bağlantısı."""
    try:
        b = await client().kitap_detay(kitap_id)
    except TymmError as e:
        return _hata(e)
    lines = [
        f"# {b['ad']}",
        f"Kaynak: {b['url']}",
        f"Etiketler: {', '.join(b['etiketler']) or '-'}",
        f"Güncelleme tarihi: {b['guncelleme_tarihi'] or '-'}",
        f"PDF: {b['pdf'] or '-'}",
    ]
    if b["etkilesimli_kitap"]:
        lines.append(f"Etkileşimli kitap: {b['etkilesimli_kitap']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Genel sayfalar
# --------------------------------------------------------------------------

SAYFALAR = {
    "Model": {
        "/genel-bakis": "Genel bakış",
        "/temel-yaklasim": "Temel yaklaşım",
        "/ogrenci-profili": "Öğrenci profili",
        "/surec": "Süreç",
        "/ortak-metin": "Ortak metin",
        "/turkiye-yuzyilinin-tarihi-temelleri": "Türkiye Yüzyılının tarihî temelleri",
        "/icerik-cercevesi": "İçerik çerçevesi",
    },
    "Beceriler ve değerler": {
        "/alan-becerileri": "Alan becerileri",
        "/beceriler/kavramsal-beceriler": "Kavramsal beceriler (KB)",
        "/beceriler/egilimler": "Eğilimler (E)",
        "/beceriler/sosyal-duygusal-ogrenme-becerileri": "Sosyal-duygusal öğrenme becerileri (SDB)",
        "/beceriler/okuryazarlik-becerileri": "Okuryazarlık becerileri (OB)",
        "/beceriler/fiziksel-beceriler": "Fiziksel beceriler",
        "/beceriler/erdem-deger-eylem-cercevesi": "Erdem-değer-eylem çerçevesi (D)",
    },
    "Planlama ve kılavuzlar": {
        "/okul-temelli-planlama": "Okul temelli planlama",
        "/taslak-cerceve-planlari/temel-egitim": "Taslak çerçeve planları — Temel eğitim",
        "/taslak-cerceve-planlari/ortaogretim": "Taslak çerçeve planları — Ortaöğretim",
        "/program-okuryazarligi-kilavuzlari": "Program okuryazarlığı kılavuzları",
        "/veli-ogrenci-bilgilendirme-kilavuzlari": "Veli-öğrenci bilgilendirme kılavuzları",
        "/telafi-programlari": "Telafi programları",
        "/program-disi-etkinlikler": "Program dışı etkinlikler",
    },
    "Materyaller": {
        "/ogretim-materyalleri": "Öğretim materyalleri",
        "/etkinlik-kitaplari": "Etkinlik kitapları",
        "/brosurler": "Broşürler",
        "/raporlar": "Raporlar",
        "/egitim-videolari": "Eğitim videoları",
        "/tanitim-videolari": "Tanıtım videoları",
        "/ders-kitabi-tanitim-videolari": "Ders kitabı tanıtım videoları",
        "/sinif-ici-etkinlik-videolari": "Sınıf içi etkinlik videoları",
    },
}


@mcp.tool(annotations=RO)
async def sayfalari_listele() -> str:
    """Sitedeki genel bilgi sayfalarını (model, beceri çerçeveleri, planlar, kılavuzlar, materyaller) yollarıyla listeler.

    Bir sayfanın içeriği için sayfa_oku aracını kullanın.
    """
    lines = ["# tymm.meb.gov.tr sayfaları", ""]
    for grup, sayfalar in SAYFALAR.items():
        lines.append(f"## {grup}")
        lines += [f"- {ad}: `{yol}`" for yol, ad in sayfalar.items()]
        lines.append("")
    return "\n".join(lines)


@mcp.tool(annotations=RO)
async def sayfa_oku(
    yol: Annotated[str, Field(description="Sayfa yolu ya da tam URL, ör. '/beceriler/kavramsal-beceriler' veya 'https://tymm.meb.gov.tr/genel-bakis'.")],
    baslangic: Annotated[int, Field(ge=0, description="Uzun sayfalarda metnin kaçıncı karakterinden başlanacağı.")] = 0,
    max_karakter: Annotated[int, Field(ge=1000, le=100000, description="Döndürülecek en fazla karakter sayısı.")] = 25000,
) -> str:
    """tymm.meb.gov.tr üzerindeki herhangi bir sayfanın ana içeriğini düz metin/Markdown olarak okur.

    Beceri tanımları (KB, E, SDB, OB, D kodları), model açıklamaları, kılavuz ve materyal listeleri için kullanılır.
    """
    try:
        s = await client().sayfa(yol)
    except TymmError as e:
        return _hata(e)
    metin = s["metin"]
    parca = metin[baslangic : baslangic + max_karakter]
    out = [f"# {s['baslik']}", f"Kaynak: {s['url']}", "", parca]
    if baslangic + max_karakter < len(metin):
        out.append(
            f"\n…(metin {len(metin)} karakter; devamı için baslangic={baslangic + max_karakter} ile tekrar çağırın)"
        )
    return "\n".join(out)


def main() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
