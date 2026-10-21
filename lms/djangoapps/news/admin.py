from django.contrib import admin

from .forms import NewsForm
from .models import News
from .sdg4.constants import NEWS_PREFIX


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    form = NewsForm
    list_display = ('title', 'created_at')
    search_fields = ('title', 'content')

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .exclude(content_type=News.ContentType.SDG4)
            .exclude(title__startswith=NEWS_PREFIX)
        )
