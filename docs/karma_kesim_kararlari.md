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
2. Atölye karma kesim ekranı + karma oturum (istasyon başına tek açık).
3. Fire raporu (karma kesim kayıtları: fire mm / oranı, testere-çene-uç ayrımı).

## Bilinen sınırlar (Dilim 1)
- Emirsiz (serbest) satırın profil tüketimi için "açık ÜS'lere ayrılmış stok" onay sorusu (emirsiz üretimdeki `AyrilmisStokOnayGerekli`) henüz yok.
- Kayıt başına ayrı 15x→15x maliyet aktarım fişi oluşur (hesaplar aynıysa fiş yazılmaz).
