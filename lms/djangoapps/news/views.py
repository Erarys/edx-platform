from django.shortcuts import get_object_or_404
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News

def news_list(request):
    news = News.objects.all()
    return render_to_response('news/news_list.html', {'news_list': news})

def news_detail(request, news_id):
    news_item = get_object_or_404(News, id=news_id)
    return render_to_response('news/news_detail.html', {'news': news_item})