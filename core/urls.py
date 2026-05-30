from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.fis_ekle, name="fis_ekle"),
    path("fis/<int:pk>/", views.fis_detay, name="fis_detay"),
    path("mizan/", views.mizan_gorunum, name="mizan"),
    path("bilanco/", views.bilanco_gorunum, name="bilanco"),
    path("gelir-tablosu/", views.gelir_tablosu_gorunum, name="gelir_tablosu"),
    path("mizan-usd/", views.mizan_usd_gorunum, name="mizan_usd"),
    path("bilanco-usd/", views.bilanco_usd_gorunum, name="bilanco_usd"),
    path("gelir-tablosu-usd/", views.gelir_tablosu_usd_gorunum, name="gelir_tablosu_usd"),
]
