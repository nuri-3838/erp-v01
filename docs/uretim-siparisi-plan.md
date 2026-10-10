# Üretim Siparişi (satış siparişinden) + stok ayırma + istasyon emirleri — plan ve durum

Bu dosya işin **tek güncel durum kaydıdır**: her adım commit edildikçe tablo güncellenir. Analiz 2026-10-10'da onaylandı;
kararlar aşağıda. Ara deploy YOK — tüm adımlar `main`'de birikir, canlıya alma ayrı talimatla.

## Kararlar (kullanıcı, 2026-10-10)

1. `UretimEmri` genişletilir (yeni model yok). Ekran adı "Üretim Siparişleri"; yeni numara `ÜS-yyyy-nnnn`, eski `UE-` kayıtlar aynen.
2. Manuel (siparişsiz) ÜS de yeni akışı kullanır. Canlıdaki kayıtsız 1 eski emir deploy'da silinir.
3. Kullanılabilir kuralı **yumuşak**: hiçbir çıkış engellenmez; kullanılabilir eksiye düşünce ÜS'de ve ilgili ekranlarda uyarı.
4. Ürün ağacı malzeme sekmesi: Ayrılan, Kullanılabilir, kullanılabilirden Eksik. İhtiyaç Hesapla varsayılan BRÜT, "stoku düş" seçmeli.
5. Sevk = depolu satış faturası onayı; deposuz fatura ÜS'yi kapatmaz, uyarı verir. Satış irsaliyesi ayrı iş (yok).
6. Onaylı satış siparişi kalem düzenlemesi açılır → aynı işlemde ÜS revize. Siparişli ÜS kalemi = sipariş kalem miktarı (aşamaz, farklı olamaz).
7. Kayıt hedefi kalanı aşabilir (uyarıyla), fazlası serbest stok. Fason firesi sonrası kalan açık kalır.
8. Tam boy ve PARÇALA fazlası serbest stok (ayrılmaz).
9. Başlamış ÜS kalıcı silinmez, yalnız İPTAL.

## Tasarım özeti

- **Ayırma** (`StokAyirma`): (ÜS, stok) başına tek aktif satır, miktar ≥ 0; stok hareketi değil. Kullanılabilir = eldeki − açık ÜS'lerin
  ayrılan toplamı (tüm depolar); `haric_emir` ile ÜS kendi ayırdığını kullanılabilir görür. Tek yazma noktası `stok_ayirma.ayirma_ayarla/ekle`.
- **Net ihtiyaç**: `ihtiyac_hesapla(kullanilabilir=)` — `None` iken kod yolu birebir aynı (golden değişmez). Verilince her seviyede
  ayrılan = min(talep, kalan kullanılabilir), net = talep − ayrılan, çalıştırma netten (tam boy ⌈·⌉, PARÇALA max), fazla serbest.
- **ÜS açılışı**: net plan → `StokAyirma` (mamul/ara/hammadde) + her operasyon için `IstasyonEmri` (planlanan/tamamlanan çalıştırma
  biriminde, durum BEKLIYOR/BASLADI/BITTI/IPTAL). Taslak `OperasyonKaydi` otomatik açılmaz.
- **Kayıt**: emirden açılır (`OperasyonKaydi.istasyon_emri`), hedef varsayılan kalan; onayda girdi tüketimi ayırmayı düşer, sürücü çıktı girişi
  ÜS'nin kalan ihtiyacına kadar ayrılır, tamamlanan gelenden artar. Fason: istasyon 10 emrinden fason dönüşü açılır.
- **Revize**: kullanılabilir(haric=ÜS) ile yeniden plan; ayırmalar hedefe çekilir; emir `planlanan = tamamlanan + yeni üretilecek`;
  başlamamış ve gereksiz emir IPTAL; taslak kayıt hedefi kalanı aşarsa düşürülür; sevk edilenin altı reddedilir; `UretimEmriRevizyon` logu.
- **Kapanış**: depolu satış faturası onayı → mamul ayırması düşer, tüm emirler BITTI/IPTAL ise ÜS KAPALI; fatura silinince yeniden açılır.

## Adımlar ve durum

| # | Adım | Durum | Commit |
|---|------|-------|--------|
| 1 | `StokAyirma` + `stok_ayirma` servisi (kullanılabilir) + ürün ağacı malzeme sekmesi Ayrılan/Kullanılabilir/Eksik (ekran + Excel) | TAMAM | `09233a8` |
| 2 | Motor: `ihtiyac_hesapla(kullanilabilir=)`; İhtiyaç Hesapla "stoku düş" seçeneği | TAMAM | (bu commit) |
| 3 | Şema: ÜS durum/revizyon alanları, `UretimEmriKalemi.siparis_kalem`, `IstasyonEmri`, `OperasyonKaydi.istasyon_emri`, `UretimEmriRevizyon`; ÜS-numara; sipariş servis kilitleri | bekliyor | |
| 4 | ÜS açılış servisi (siparişten + manuel): ayırma + istasyon emirleri; taslak kayıt açma kaldırılır | bekliyor | |
| 5 | Emirden kayıt açma; onay/geri alma etkileri (ayırma, tamamlanan, durum) | bekliyor | |
| 6 | Fason dönüşü emre bağlama, fire | bekliyor | |
| 7 | Revize algoritması + geçmiş; onaylı sipariş kalem düzenleme → ÜS revize | bekliyor | |
| 8 | Kapanış (satış faturası) + yeniden açılış; deposuz fatura uyarısı | bekliyor | |
| 9 | Ekranlar: ÜS liste/detay, istasyon emirleri (yeni ekran kodu `istasyon_emirleri`), kayıt listesi/formu, sipariş detayı rozeti | bekliyor | |
| 10 | Deploy (ayrı talimat): yedek, migrate, canlıdaki eski emrin silinmesi, yetki ataması | bekliyor | |

## Adım notları

- **Adım 1:** Ayırma kümesi şimdilik "silinmemiş ÜS" ile sınırlı; adım 3'te `durum=ACIK` filtresi eklenir (`stok_ayirma.acik_ayirmalar`).
  Ürün ağacı malzeme sekmesinde depo filtresi seçiliyse eldeki o depodan, ayrılan tüm depolardan; kullanılabilir eksiye düşebilir (`asim`).
  Mevcut `uretim_emri_olustur` davranışı (taslak kayıt açma) bu adımda DEĞİŞMEDİ.
- **Adım 2:** `ihtiyac_hesapla(kullanilabilir=)` — sözlük ya da çağrılabilir (zincir keşfinden sonra stok pk listesiyle tek çağrı,
  `stok_ayirma.kullanilabilir_haritasi`). Ayırma her stok için TEK kez: sürücü çıktılar kendi operasyonunda (seviye sırası, toplanmış talep),
  yapraklar operasyon döngüsünden sonra. Plan/özet satırlarına `ayrilan`, `net`, `eksik` (yaprak); ağaç düğümlerine `ayrilan_toplam`,
  `net_toplam`, `eksik_toplam`; dönüşe `net_mod`, `ayrilan` haritası. Tam boy olmayan dalda çocuk talebi net/ihtiyaç oranıyla küçülür.
  `pay_dus=True` ile birlikte kullanım öngörülmedi (maliyet görünümleri brüt). Brüt yol (None) birebir aynı — golden geçti.
  İhtiyaç Hesapla ekranı: "Kullanılabilir stoğu düş" onay kutusu (varsayılan kapalı = brüt); özet tablosuna "Stoktan Ayrılan" ve
  "Net İhtiyaç / Eksik" sütunları, ağaçta "stoktan X · net Y" / "eksik" notu.
