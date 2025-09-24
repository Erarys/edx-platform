from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News


def get_lang(request):
    lang = request.GET.get("lang", "ru")  # по умолчанию русский
    if lang not in ["ru", "en", "kz"]:
        lang = "ru"
    return lang


def news_list(request):
    lang = get_lang(request)
    news = News.objects.all()
    return render_to_response('news/news_list.html', {'news': news, 'lang': lang})


def news_detail(request, news_id):
    lang = get_lang(request)
    news_item = News.objects.get(id=news_id)
    return render_to_response('news/news_detail.html', {'news_item': news_item, 'lang': lang})
