from django.urls import path
from .views import RegisterView, SignInView, SignOutView


urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('signin/',   SignInView.as_view(),   name='signin'),
    path('signout/',  SignOutView.as_view(),  name='signout'),
]