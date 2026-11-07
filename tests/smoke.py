import asyncio, sys
from tymm_mcp import server as s

async def run(name, coro, n=900):
    out = await coro
    print(f"\n=================== {name} ({len(out)} chars)\n{out[:n]}")

async def main():
    await run("kademeleri_listele", s.kademeleri_listele(), 1200)
    await run("ders_ara kimya", s.ders_ara("kimya"))
    await run("ders_ara matematik temel", s.ders_ara("matematik", "temel eğitim"))
    await run("sinifa_gore_dersler 2/6", s.sinifa_gore_dersler("2", 6), 700)
    await run("ders_programi kimya", s.ders_programi("kimya-dersi"))
    await run("ders_programi ambiguous", s.ders_programi("matematik"))
    await run("uniteleri_listele", s.uniteleri_listele("kimya-dersi", 11))
    await run("unite_detay filtered", s.unite_detay("kimya-dersi", 92, ["Ders Saati", "Anahtar Kavramlar"]))
    await run("unite_detay full", s.unite_detay("kimya-dersi", 92), 2500)
    await run("ogrenme_ciktilari", s.ogrenme_ciktilari("kimya-dersi", 11), 1500)
    await run("kitap_ara", s.kitap_ara("kimya", sinif_id=11))
    await run("kitap_detay", s.kitap_detay(71))
    await run("sayfa_oku", s.sayfa_oku("/beceriler/egilimler", max_karakter=1500), 1800)
    await run("sayfa_oku bad host", s.sayfa_oku("https://example.com/x"))
    await run("unite bad", s.unite_detay("kimya-dersi", 999999))
    await s.client().aclose()
asyncio.run(main())
