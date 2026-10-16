from django import forms
from .models import News
from .sdg4.constants import NEWS_PREFIX
from django.utils.translation import gettext_lazy as _

class NewsForm(forms.ModelForm):
    def clean_title(self):
        title = self.cleaned_data['title']
        if title.startswith(NEWS_PREFIX):
            raise forms.ValidationError(_("This title prefix is reserved for SDG 4 resources."))
        return title

    class Meta:
        model = News
        fields = ['title', 'content', 'image']
        widgets = {
            'content': forms.Textarea(attrs={'rows': 10}),
        }
