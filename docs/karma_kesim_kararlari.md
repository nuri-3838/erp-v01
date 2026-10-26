# Karma kesim (basamak kesiminde operatör karması) — kararlar

Sorun: 40 BASAMAK KESİM'de kayıt tam boy zorunlu ve bir boydan tek basamak ölçüsü çıkıyor; sahada aynı boydan farklı basamaklar birlikte kesiliyor.

## Onaylanan kararlar
1. **Kayıt grubu (A):** `KarmaKesim` başlığı + her (parça × emir) ayrımı normal bir `OperasyonKaydi` (`OperasyonKaydi.karma`). Onay/stok/maliyet/ayırma/geri alma mevcut akışla aynı; mevcut tek ölçülü kayıt akışı DEĞİŞMEZ.
2. **Uzunluk kontrolü:** `Σ(adet × parça mm) + toplam adet × testere_payi ≤ boy sayısı × (boy mm − cene_payi)`; aşarsa reddedilir.
3. **Fire = boy sayısı × boy mm − Σ(adet × parça mm)** = testere + çene + uç. Stoğa girmez, ayrıca maliyetlenmez; kayıtta (`KarmaKesim`) ve raporda ayrı gösterilir.
4. **Maliyet:** profilin TOPLAM maliyeti (fire dahil) parçalara kesilen uzunluk oranıyla dağılır: parça birim maliyeti = toplam × parça mm / Σ(adet × parça mm). Profil payı kayıtlara `boy sayısı × (kayıt uzunluğu / toplam)` (6 ondalık, fark en uzun kayda).
5. **Emir bağı:** parçanın açık istasyon emirleri `uretim.siralama` (Acil → termin → ÜS tarihi/no) sırasıyla **net kalan ihtiyaç** kadar doldurulur; artan adet emirsiz kayıtla serbest stoğa girer.
6. **Emir kapanışı:** emrin net ihtiyacı karşılanınca `planlanan` `tamamlanan`a çekilir (tam boy planı dolmasa da emir BITTI olur); eski değer `OperasyonKaydi.karma_iz`de saklanır, geri almada döner.
7. **Oturum:** istasyon başına tek açık karma oturum; tek ekip + sorumlu kuralları aynen geçerli, adedi sorumlu girer (Dilim 2).

## Parametreler
`UretimAyari.testere_payi_mm` = 3 (parça başına), `UretimAyari.cene_payi_mm` = 75 (boy başına); `Stok.boy_uzunluk_mm` (profil kartı, 150-10-0011 = 6000). Ayar değerleri kayıt anında `KarmaKesim`e kopyalanır (geçmiş kayıt etkilenmez).

## Veri göçü
`python manage.py karma_boy_goc` (KURU) → 13 basamak operasyonunun `boy_mm` değerini stok adından ("(349,73 MM)") okur ve profil boyunu yazar; `--uygula` ile yazar.

## Dilimler
1. Veri alanları + ayar + servis (`core/services/karma_kesim.py`) + testler. ✔
2. Atölye karma kesim ekranı + karma oturum (istasyon başına tek açık). ✔ (`IsOturumu.karma`, `is_oturumu.baslat_karma / bitir_karma / adet_gir_karma`, istasyon sayfasında "Karma kesim"
   bölümü, canlı önizleme, bugünkü son karma kesimi geri alma). Oturum kuralları: tek ekip + sorumlu aynen; aynı istasyonda aynı profili tüketen emirle çakışma iki yönde engellenir.
   Yönetici karma adedini girmez (sorumlu atölyeden girer); sorumlu değiştirme / iptal yönetici işlemidir.
3. Fire raporu (karma kesim kayıtları: fire mm / oranı, testere-çene-uç ayrımı).

## Ofis karma kesim (Emirsiz Üretim › Karma kesim) — onaylanan kararlar D1–D5
- **D1** `KarmaKesim.kaynak` (ATOLYE / OFIS, migration 0247). Ofis kaydı: iş oturumu, mesai ve işçilik süresi YOK; "Giren" = personel alanı (boşsa kullanıcıya bağlı personel, yoksa kullanıcı adı).
- **D2** "Karma Kesimler" listesi (`/uretim/karma-kesim/`: kaynak / istasyon / tarih / ara süzgeci) + detay (kaynak, uzunluk ve fire dökümü testere / çene / uç, parçalar, kayıtlar, geri al).
  Operasyon Kayıtları'nda karma rozeti (KK no · Atölye/Ofis) ve "Kaynak" süzgecinde Karma kesim (tümü / atölye / ofis).
- **D3** Karma kaydı tek başına "Geri al ve sil" ile geri alınamaz (gruba yönlendirir); geri alma karma kesim sayfasından "Tümünü geri al" (atölye ve ofis kaynaklı için).
- **D4** Parça girişi: profilin aday parçaları liste halinde (kod, mm, ihtiyaç ipucu, adet kutusu); canlı önizleme `emirsiz_karma_onizleme` (yazmaz).
- **D5** Ofiste profil, başka açık ÜS'lere ayrılmış stoktan tüketilecekse onay kutusu (kesimin parça verdiği ÜS'lerin kendi profil ayırması, o ÜS'ye düşen pay kadar kullanılabilir sayılır). Atölye aynen kalır.
- **Çakışma:** istasyonda aynı profil için açık / "Adet bekliyor" atölye karma oturumu ya da aynı profili tüketen açık tekil oturum varsa ofis kaydı engellenir ("Bu istasyonda X'in ekibi karma kesim
  yapıyor"); yalnız süper kullanıcı notla aşar (`IsOturumuOlay.OFIS_ASILDI`; atölye oturumu açık kalır). Servis: `karma_kesim.ofis_karma_kes` (istasyon satırı kilitlenir).

## Bilinen sınırlar (Dilim 1)
- Emirsiz (serbest) satırın profil tüketimi için "açık ÜS'lere ayrılmış stok" onay sorusu (emirsiz üretimdeki `AyrilmisStokOnayGerekli`) henüz yok.
- Kayıt başına ayrı 15x→15x maliyet aktarım fişi oluşur (hesaplar aynıysa fiş yazılmaz).
