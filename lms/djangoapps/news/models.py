from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Article(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="articles")
    title = models.CharField(max_length=255)
    content = models.TextField()
    pub_date = models.DateTimeField(default=timezone.now)
    image = models.ImageField(upload_to="articles/", blank=True, null=True)  # 👈 будет в static/images/articles/

    def __str__(self):
        return self.title
