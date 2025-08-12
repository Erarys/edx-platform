from django.db import models
from django.contrib import admin

# Create your models here.

class Univeruser(models.Model):
	edx_id = models.IntegerField(null=True)
	edx_pass = models.CharField(max_length=300, null=True)
	univer_id = models.CharField(max_length=300, null=True)
	edx_username = models.CharField(max_length=300, null=True)

admin.site.register(Univeruser)