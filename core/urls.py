from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.fis_ekle, name="fis_ekle"),
    path("fis/<int:pk>/", views.fis_detay, name="fis_detay"),
    path("mizan/", views.mizan_gorunum, name="mizan"),
]
