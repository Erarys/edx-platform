from django.conf import settings
from django.db import models


class News(models.Model):
    class ContentType(models.TextChoices):
        NEWS = "news", "Новость"
        SDG4 = "sdg4", "Материал ЦУР 4"

    title = models.CharField(max_length=200, verbose_name="Заголовок")
    content = models.TextField(verbose_name="Содержание")
    content_type = models.CharField(
        max_length=16,
        choices=ContentType.choices,
        default=ContentType.NEWS,
        db_index=True,
        verbose_name="Тип материала",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="news_articles",
        blank=True,
        null=True,
        verbose_name="Автор",
    )
    cover_image = models.ImageField(
        upload_to="articles/covers/",
        verbose_name="Обложка публикации",
        blank=True,
        null=True,
    )
    image = models.ImageField(
        upload_to='articles/',
        verbose_name="Изображение",
        blank=True,
        null=True
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    published_at = models.DateTimeField(blank=True, null=True, verbose_name="Дата публикации")
    view_count = models.PositiveBigIntegerField(default=0, verbose_name="Количество просмотров")

    class Meta:
        verbose_name = "Новость"
        verbose_name_plural = "Новости"
        ordering = ['-created_at']

    def __str__(self):
        return self.title

