"""Fixed public routes; staff routes precede the faculty slugs."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="sdg4_index"),
    path("language/", views.change_language, name="sdg4_language"),
    path("moderation/", views.moderation, name="sdg4_moderation"),
    path("moderation/<int:news_id>/approve/", views.approve, name="sdg4_approve"),
    path("upload/start/", views.chunk_upload_start, name="sdg4_upload_start"),
    path("upload/chunk/", views.chunk_upload_part, name="sdg4_upload_chunk"),
    path("upload/complete/", views.chunk_upload_complete, name="sdg4_upload_complete"),
    path("manage/", views.manage, name="sdg4_manage"),
    path("manage/new/", views.edit, name="sdg4_create"),
    path("manage/<int:news_id>/delete/", views.delete_publication, name="sdg4_delete"),
    path("manage/<int:news_id>/", views.edit, name="sdg4_edit"),
    path("<slug:faculty>/", views.faculty, name="sdg4_faculty"),
    path("<slug:faculty>/<slug:teacher>/", views.teacher, name="sdg4_teacher"),
    path(
        "<slug:faculty>/<slug:teacher>/resource/<slug:material_id>/",
        views.resource,
        name="sdg4_resource",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/resource/<slug:material_id>/download/",
        views.resource,
        {"download": True},
        name="sdg4_resource_download",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/resource/<slug:material_id>/preview/",
        views.resource_preview,
        name="sdg4_resource_preview",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/article/<slug:block_id>/",
        views.article_block,
        name="sdg4_article_block",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/article/<slug:block_id>/download/",
        views.article_block,
        {"download": True},
        name="sdg4_article_block_download",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/article/<slug:block_id>/preview/",
        views.article_block_preview,
        name="sdg4_article_block_preview",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/cover/",
        views.cover_image,
        name="sdg4_cover",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/<int:course>/<slug:material_id>/",
        views.material,
        name="sdg4_material",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/<int:course>/<slug:material_id>/download/",
        views.material,
        {"download": True},
        name="sdg4_download",
    ),
    path(
        "<slug:faculty>/<slug:teacher>/<int:course>/<slug:material_id>/preview/",
        views.material_preview,
        name="sdg4_material_preview",
    ),
]
