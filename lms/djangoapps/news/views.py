from django.shortcuts import get_object_or_404
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News, Category


def index(request):
   
    news_list = News.objects.all().order_by("-pub_date")
    return render_to_response("news/index.html", {"news_list": news_list})


def category(request, cat_id):
    
    category = get_object_or_404(Category, id=cat_id)
    news_list = category.news.all().order_by("-pub_date")
    return render_to_response("news/category.html", {"category": category, "news_list": news_list})


def article(request, news_id):
   
    news = get_object_or_404(News, id=news_id)
    return render_to_response("news/article.html", {"news": news})
