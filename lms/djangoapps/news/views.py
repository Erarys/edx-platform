from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News
from .forms import NewsForm
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
# from ..commerce.api.v1.models import Course


def news_list(request):
    news = News.objects.all()
    context = {
        'news_list': news,
        'create_url': reverse('news_create'),
    }
    return render_to_response('news/list.html', context, request=request)

def news_detail(request, news_id):  # используем news_id
    news = get_object_or_404(News, pk=news_id)  # все равно используем pk для поиска
    context = {
        'news': news,
        'list_url': reverse('news_list'),
    }
    return render_to_response('news/detail.html', context, request=request)


@user_passes_test(lambda u: u.is_staff)
def news_create(request):
    if request.method == 'POST':
        form = NewsForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('news_list')
    else:
        form = NewsForm()

    context = {
        'form': form,
        'list_url': reverse('news_list'),
    }
    return render_to_response('news/form.html', context, request=request)




def analyze(request):
    courses = CourseOverview.objects.all()

    courses_by_org = (
        CourseOverview.objects
        .values("org")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    context = {
        "courses": courses[:50],
        "courses_count": 10, # courses.count(),
        "chart_labels": [c["org"] for c in courses_by_org],
        "chart_data": [c["total"] for c in courses_by_org],
    }

    news = News.objects.all()
    context = {
        'news_list': news,
        'create_url': reverse('news_create'),
    }

    return render(request, "news/analyze.html", context)

