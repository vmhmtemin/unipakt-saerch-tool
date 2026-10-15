# UniPakt Davet Paneli

İstanbul'daki üniversite kulüplerine Instagram üzerinden davet göndermeyi hızlandıran ekip paneli.

```
GitHub Actions  → scraper/kulup_bulucu.py çalışır → kulüpleri Supabase'e ekler
GitHub Pages    → index.html (panel)
Supabase        → UniPakt.Davet projesi: kulüpler, durumlar, kim yazdı, notlar
```

- **Panel**: üniversiteler listesi, tıklayınca kulüpleri açılır. Kulübe tıkla → Instagram profilini aç, hazır mesajı kopyala, durumu işaretle.
- **Takvim**: kulüp penceresinden görüşme ekle (tarih, saat, kiminle, bizden kim, link). Takvim sekmesinde ay görünümü ve
  yaklaşan görüşmeler listesi; her görüşme tek tıkla Google Takvim'e de eklenebilir.
- **Tarama**: GitHub'da tek tuşla çalışır, bilgisayarının açık olması gerekmez.
- Tarama **yeni kulüpleri ekler, mevcut kulüplerin durumuna/notuna asla dokunmaz.** Sadece boş kalmış Instagram/e-posta alanını doldurabilir.

```
unipakt-saerch-tool/
├── index.html                      ← panel
├── robots.txt / .nojekyll          ← Google'da çıkmasın / GitHub Pages ayarı
├── scraper/
│   ├── kulup_bulucu.py             ← tarayıcı script
│   ├── universiteler.json          ← taranacak üniversiteler
│   ├── requirements.txt
│   └── sayfalar/                   ← elle kaydedilen sayfalar (robots engeli olanlar)
└── .github/workflows/kulup-tara.yml ← GitHub Action
```

---

## Kurulum (bir kez)

### 1. Supabase: giriş hesabını oluştur

Veritabanı (`UniPakt.Davet` projesi) hazır: tablo, güvenlik kuralları ve pilot 228 kulüp yüklü.
Eksik olan tek şey panele giriş hesabı. Şifre sadece sende kalsın diye bunu sen oluşturuyorsun:

1. [supabase.com/dashboard](https://supabase.com/dashboard) → **UniPakt.Davet** projesi
2. **Authentication → Users → Add user → Create new user**
3. E-posta: `iletisim@unipakt.com`, güçlü bir şifre belirle, **Auto Confirm User** işaretli olsun.
4. **Authentication ayarlarında yeni kullanıcı kaydını (sign up) kapat.** Panel zaten sadece izin listesindeki e-postaya veri gösteriyor; bu ikinci bir kilit.

> Başka bir e-posta ile giriş yapılacaksa önce izin listesine eklenmesi gerekir:
> Supabase → **SQL Editor** → `insert into izinli_kullanicilar (email) values ('yeni@adres.com');`

### 2. GitHub: repoyu oluştur

1. GitHub'da yeni bir repo aç (örn. `unipakt-saerch-tool`).
   - **Public** repo: GitHub Pages ve Actions ücretsiz. Repoda gizli bilgi yok; kulüp verisi Supabase'de, girişsiz görünmez.
   - **Private** isteniyorsa GitHub Pages için GitHub Pro gerekir (GitHub Student Developer Pack ile ücretsiz).
2. Bu klasördeki **tüm dosyaları** repoya yükle.
   - Tarayıcıdan yüklerken macOS gizli klasörleri (`.github`) göstermeyebilir. O durumda GitHub'da
     **Add file → Create new file** de, dosya adına `.github/workflows/kulup-tara.yml` yaz ve içeriğini yapıştır.
   - Ya da terminalden: `git init && git add . && git commit -m "ilk" && git branch -M main && git remote add origin <repo-url> && git push -u origin main`

### 3. GitHub: Supabase anahtarlarını gizli olarak ekle

Repo → **Settings → Secrets and variables → Actions → New repository secret**

| Ad | Değer |
|---|---|
| `SUPABASE_URL` | `https://htybytusahvzbduzkdpv.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Supabase → UniPakt.Davet → **Project Settings → API Keys** → **secret** anahtar |

> ⚠️ `SUPABASE_SERVICE_KEY` tam yetkili anahtardır. Sadece GitHub Secrets'a yapıştır; koda, sohbete, ekran görüntüsüne koyma.

### 4. GitHub Pages'i aç

Repo → **Settings → Pages → Build and deployment → Deploy from a branch → `main` / `(root)` → Save**

Bir-iki dakika sonra panel `https://vmhmtemin.github.io/unipakt-saerch-tool/` adresinde açılır.
Bu linki Ömer ve Uğur'la paylaş; ortak hesapla giriş yapıp **"Ben:"** kısmından kendi adını seçsinler (tarayıcı hatırlar).

---

## Kullanım

### Kulüp taraması

Repo → **Actions → Kulüp tarama → Run workflow**

- **"Sadece…"** kutusu boşsa `universiteler.json`'daki tüm üniversiteler taranır.
- Tek üniversite için adının bir kısmını yaz (örn. `Maltepe`).
- Bitince özet, işin loglarında görünür; `kulupler` adlı sonuç dosyası da indirilebilir.
- **"Sadece yükle"** kutusunu işaretlersen tarama yapılmaz: son taramanın sonuç dosyası temizlenip Supabase'e
  tekrar yüklenir. Tarama bitti ama yükleme bir hatayla yarıda kaldıysa saatlerce yeniden taramak yerine bunu kullan.
- Haftalık otomatik tarama istersen `kulup-tara.yml` içindeki `schedule` satırlarının başındaki `#` işaretlerini kaldır.

### Yeni üniversite ekleme

GitHub'da `scraper/universiteler.json` dosyasını aç → kalem ikonu → blok ekle → Commit:

```json
{
  "ad": "Maltepe Üniversitesi",
  "kaynaklar": ["https://www.maltepe.edu.tr", "sayfalar/maltepe.html"]
}
```

Kulüp listesi sayfasının tam adresini biliyorsan ana sayfa yerine onu yaz; daha hızlı ve isabetli olur.

### "robots.txt izin vermiyor" ya da hiç kulüp çıkmıyorsa

Bazı siteler otomatik erişimi yasaklıyor ya da listeyi JavaScript ile sonradan yüklüyor. O sayfayı tarayıcıda aç,
**Cmd+S → "Web Sayfası, Yalnızca HTML"** ile kaydet ve GitHub'da `scraper/sayfalar/` klasörüne yükle
(`universiteler.json`'da yazan adla). Sonra taramayı tekrar çalıştır.

### Paneli düzenleme

`index.html` dosyasının en üstündeki **AYARLAR** bölümünde:

- `MESAJ` — davet metni (`{SELAM}` kulüp adıyla dolar)
- `KISILER` — "Ben:" listesindeki isimler
- `DURUMLAR` — durum listesi (değiştirirsen veritabanındaki izin verilen değerlerin de güncellenmesi gerekir)

### Kendi bilgisayarında çalıştırmak (isteğe bağlı)

```bash
cd scraper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python kulup_bulucu.py --sadece Bilgi        # sadece dosyaya yazar (cikti/)
python kulup_bulucu.py --temizle             # mevcut çıktıyı yeniden temizler
```

`SUPABASE_URL` ve `SUPABASE_SERVICE_KEY` ortam değişkenleri tanımlıysa yerelde de Supabase'e yükler.

---

## Bilinen sınırlar

- GitHub'ın sunucuları yurt dışında; bazı üniversite siteleri yabancı IP'leri engelleyebilir. Öyle bir site olursa
  o üniversiteyi kendi bilgisayarından tara ya da sayfayı elle kaydet.
- Birçok üniversite sitesinde kulüplerin Instagram hesabı yazmıyor. Panelde **"Instagram'da ara"** butonuyla bulup
  kaydetmek gerekiyor; bir kez kaydedilen hesap üçünüze de görünür.
- Instagram'a hiçbir otomatik istek atılmaz; mesajlar elle gönderilir (hesap güvenliği için bilinçli tercih).
