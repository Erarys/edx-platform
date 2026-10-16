from django.urls import include, path

urlpatterns = [
    path("sdg4/", include("lms.djangoapps.news.sdg4.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
]
