from django.shortcuts import render, get_object_or_404, redirect
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News
from .forms import NewsForm

def news_list(request):
    news = News.objects.all()
    return render_to_response('news/list.html', {'news_list': news})

def news_detail(request, pk):
    news = get_object_or_404(News, pk=pk)
    return render_to_response('news/detail.html', {'news': news})

def news_create(request):
    if request.method == 'POST':
        form = NewsForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('news_list')
    else:
        form = NewsForm()
    return render_to_response('news/form.html', {'form': form})