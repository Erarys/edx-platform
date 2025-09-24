from django.contrib import admin
from django import forms
from .models import News

class NewsAdminForm(forms.ModelForm):
    content = forms.CharField(widget=forms.Textarea(attrs={'rows': 30, 'cols': 120}))
    
    class Meta:
        model = News
        fields = '__all__'

@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    form = NewsAdminForm
    list_display = ('title', 'created_at', 'updated_at')
    search_fields = ('title', 'content')
    list_filter = ('created_at',)
    date_hierarchy = 'created_at'