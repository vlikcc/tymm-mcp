# tymm-mcp

[Türkiye Yüzyılı Maarif Modeli](https://tymm.meb.gov.tr) (MEB) sitesi için bir MCP sunucusu. Claude ve diğer MCP istemcileri bu sunucuyla öğretim programlarını, ünite/tema içeriklerini (öğrenme çıktıları, beceriler, ders saatleri) ve ders kitaplarını doğrudan sorgulayabilir.

> Resmî bir MEB ürünü değildir. Veriler her istekte sitenin herkese açık sayfalarından okunur ve 1 saat boyunca bellekte önbelleğe alınır.

## Araçlar

| Araç | Ne yapar |
|---|---|
| `kademeleri_listele` | Kademeleri ve sınıf kimliklerini (`sinif_id`) listeler |
| `ders_ara` | Dersleri ada ve/veya kademeye göre arar, `ders_slug` döndürür |
| `sinifa_gore_dersler` | Bir kademe + sınıftaki dersleri, program PDF'leriyle listeler |
| `ders_programi` | Dersin program PDF'i, sınıf seviyeleri ve kitapları |
| `uniteleri_listele` | Bir ders + sınıfın ünite/temaları (`unite_id`) |
| `unite_detay` | Ünitenin tüm içeriği; isteğe bağlı bölüm filtresi |
| `ogrenme_ciktilari` | Bir sınıfın **tüm** ünitelerindeki öğrenme çıktıları ve ders saatleri |
| `kitap_ara` | Ders kitabı, öğretmen kılavuzu, çalışma ve etkinlik kitaplarında arama |
| `kitap_detay` | Kitabın PDF'i, etkileşimli kitap bağlantısı ve güncelleme tarihi |
| `sayfalari_listele` | Genel bilgi sayfalarını (beceri çerçeveleri, kılavuzlar…) listeler |
| `sayfa_oku` | tymm.meb.gov.tr üzerindeki herhangi bir sayfayı metin olarak okur |

Örnek istemler:

- "9. sınıf kimya dersinin tüm öğrenme çıktılarını ve ders saatlerini çıkar."
- "5. sınıf fen bilimleri 2. ünitenin anahtar kavramlarını ve ölçme-değerlendirme kısmını getir."
- "10. sınıf matematik öğretmen kılavuz kitabının PDF bağlantısı nedir?"
- "KB2.4 çözümleme becerisi nasıl tanımlanıyor?"

## Kurulum

Python 3.10 veya üzeri gerekir.

```bash
cd tymm-mcp
python3 -m venv .venv
.venv/bin/pip install .
```

> Kodda değişiklik yaptıktan sonra `.venv/bin/pip install .` komutunu tekrar çalıştırın. Düzenlenebilir kurulum (`pip install -e .`) macOS'ta önerilmez: macOS, `.pth` dosyalarına "gizli" bayrağı koyabiliyor ve Python 3.13+ gizli `.pth` dosyalarını atladığı için sunucu MCP istemcisinden başlatıldığında `ModuleNotFoundError: tymm_mcp` hatası verir.

### Claude Code

```bash
claude mcp add tymm -- "$(pwd)/.venv/bin/tymm-mcp"
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` dosyasına ekleyin (yolu kendi klasörünüzle değiştirin):

```json
{
  "mcpServers": {
    "tymm": {
      "command": "/TAM/YOL/tymm-mcp/.venv/bin/tymm-mcp"
    }
  }
}
```

## Test

```bash
.venv/bin/python tests/smoke.py      # araç fonksiyonlarını doğrudan çağırır
.venv/bin/python tests/stdio_e2e.py  # sunucuyu MCP stdio üzerinden başlatıp test eder
```

Testler canlı siteye istek atar.

## Nasıl çalışır

Site ASP.NET MVC ile sunucu tarafında üretiliyor. Bu sunucu iki kaynaktan okur:

- **JSON uç noktaları** (sitenin kendi filtre menülerinin kullandığı): `/Ders/GetProgramList`, `/Ders/GetDerslerBySinif`, `/Kitap/GetDersKitaplariList`. Listeler 20'şerli sayfalanıyor; tüm sayfalar aynı `seed` ile çekilir.
- **HTML sayfaları**: ders (`/ogretim-programlari/ders/{slug}`), sınıf (`/ogretim-programlari/{slug}/{sinif_id}`), ünite (`/{slug}/unite/{unite_id}`) ve kitap (`/kitap/{id}/{slug}`) sayfaları BeautifulSoup ile ayrıştırılır.

Site yapısı değişirse ayrıştırıcılar `src/tymm_mcp/client.py` içinde. Siteye aynı anda en fazla 4 istek gider; `robots.txt` yalnızca `/admin` yolunu engelliyor ve bu yol okunmaz.

## Lisans

[MIT](LICENSE). Lisans yalnızca bu sunucunun kodunu kapsar; tymm.meb.gov.tr üzerindeki içeriklerin hakları MEB'e aittir.
