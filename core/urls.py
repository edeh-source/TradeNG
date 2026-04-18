from django.urls import path
from . import views

urlpatterns = [
    path("", views.homepage, name="home"),
    path("login/", views.login_user, name="signin"),
]
