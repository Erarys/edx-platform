from django import forms
from .models import retakecatalog

class RetakeCatalogForm(forms.ModelForm):
    class Meta:
        model = retakecatalog
        fields = '__all__'

