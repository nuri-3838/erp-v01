from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.pano, name="pano"),
    path("fis-gir/", views.fis_ekle, name="fis_ekle"),
    path("fisler/", views.fis_listesi, name="fis_listesi"),
    path("kurlar/", views.kurlar, name="kurlar"),
    path("api/kur-usd/", views.kur_usd_api, name="kur_usd_api"),
    path("hesap-plani/", views.hesap_plani, name="hesap_plani"),
    path("hesap-plani/ekle/", views.hesap_ekle, name="hesap_ekle"),
    path("hesap-plani/<str:kod>/ad/", views.hesap_ad_guncelle, name="hesap_ad_guncelle"),
    path("hesap-plani/<str:kod>/sil/", views.hesap_sil, name="hesap_sil"),
    path("fis/<int:pk>/", views.fis_detay, name="fis_detay"),
    path("fis/<int:pk>/duzenle/", views.fis_duzenle, name="fis_duzenle"),
    path("fis/<int:pk>/iptal/", views.fis_iptal_gorunum, name="fis_iptal"),
    path("mizan/", views.mizan_gorunum, name="mizan"),
    path("ekstre/<str:hesap_kodu>/", views.hesap_ekstresi, name="hesap_ekstresi"),
    path("bilanco/", views.bilanco_gorunum, name="bilanco"),
    path("gelir-tablosu/", views.gelir_tablosu_gorunum, name="gelir_tablosu"),
    path("mizan-usd/", views.mizan_usd_gorunum, name="mizan_usd"),
    path("bilanco-usd/", views.bilanco_usd_gorunum, name="bilanco_usd"),
    path("gelir-tablosu-usd/", views.gelir_tablosu_usd_gorunum, name="gelir_tablosu_usd"),
    # Ayarlar modülü (yalnızca yönetici)
    path("kullanicilar/", views.kullanici_listesi, name="kullanici_listesi"),
    path("kullanicilar/ekle/", views.kullanici_ekle, name="kullanici_ekle"),
    path("kullanicilar/<int:pk>/duzenle/", views.kullanici_duzenle, name="kullanici_duzenle"),
    path("ayarlar/kullanici-yetkileri/", views.kullanici_yetkileri, name="kullanici_yetkileri"),
    path("ayarlar/yedek/", views.yedek_yonetim, name="yedek"),
    path("ayarlar/yedek/indir/<str:ad>/", views.yedek_indir, name="yedek_indir"),
    # STOKLAR modülü
    path("stoklar/", views.stoklar, name="stoklar"),
    path("stoklar/kategoriler/", views.kategoriler, name="kategoriler"),
    path("stoklar/kategoriler/ekle/", views.kategori_ekle, name="kategori_ekle"),
    path("stoklar/kategoriler/<int:pk>/duzenle/", views.kategori_duzenle, name="kategori_duzenle"),
    path("stoklar/kategoriler/<int:pk>/sil/", views.kategori_sil, name="kategori_sil"),
    path("stoklar/fatura-tipleri/", views.fatura_tipleri, name="fatura_tipleri"),
    path("stoklar/fatura-tipleri/ekle/", views.fatura_tipi_ekle, name="fatura_tipi_ekle"),
    path("stoklar/fatura-tipleri/<int:pk>/duzenle/", views.fatura_tipi_duzenle, name="fatura_tipi_duzenle"),
    path("stoklar/fatura-tipleri/<int:pk>/sil/", views.fatura_tipi_sil, name="fatura_tipi_sil"),
    path("stoklar/birimler/", views.birimler, name="birimler"),
    path("stoklar/birimler/ekle/", views.birim_ekle, name="birim_ekle"),
    path("stoklar/birimler/<int:pk>/duzenle/", views.birim_duzenle, name="birim_duzenle"),
    path("stoklar/birimler/<int:pk>/sil/", views.birim_sil, name="birim_sil"),
]
