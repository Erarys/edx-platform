from edxmako.shortcuts import marketing_link, render_to_response, render_to_string
from .models import retakecatalog
from .forms import RetakeCatalogForm
from django.db.models import Q
from django.middleware.csrf import get_token

def retake_catalog(request):
    courses = retakecatalog.objects.all()

    course_name = request.GET.get('course_name')
    course_language = request.GET.get('course_language')
    difficulty_level = request.GET.get('difficulty_level')
    course_type = request.GET.get('course_type')
    platform = request.GET.get('platform')
    education_field = request.GET.get('education_field')
    training_direction = request.GET.get('training_direction')
    credit_workload = request.GET.get('credit_workload')
    workload_hours = request.GET.get('workload_hours')
    workload_credits = request.GET.get('workload_credits')
    related_disciplines = request.GET.get('related_disciplines')
    if course_name:
        courses = courses.filter(course_name__icontains=course_name)

    if course_language:
        courses = courses.filter(course_language=course_language)
    if difficulty_level:
        courses = courses.filter(difficulty_level=difficulty_level)
    if course_type:
        courses = courses.filter(course_type=course_type)
    if platform:
        courses = courses.filter(platform=platform)
    if education_field:
        courses = courses.filter(education_field__icontains=education_field)
    if training_direction:
        courses = courses.filter(training_direction__icontains=training_direction)
    if credit_workload:
        if credit_workload.startswith(">="):
            courses = courses.filter(credit_workload__gte=float(credit_workload[2:]))
        else:
            courses = courses.filter(credit_workload__lt=float(credit_workload[1:]))
    if workload_hours:
        if workload_hours.startswith(">="):
            courses = courses.filter(workload_hours__gte=float(workload_hours[2:]))
        else:
            courses = courses.filter(workload_hours__lt=float(workload_hours[1:]))
    if workload_credits:
        if workload_credits.startswith(">="):
            courses = courses.filter(workload_credits__gte=float(workload_credits[2:]))
        else:
            courses = courses.filter(workload_credits__lt=float(workload_credits[1:]))
    if related_disciplines:
        courses = courses.filter(related_disciplines__icontains=related_disciplines)

    return render_to_response("retake_catalog/retake_catalog.html", {'courses': courses})




def retake_catalog_form_view(request):
    if request.method == 'POST':
        form = RetakeCatalogForm(request.POST)
        if form.is_valid():
            form.save()
            return render_to_response('retake_catalog/success.html', {})
    else:
        form = RetakeCatalogForm()
    csrf_input = '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'.format(get_token(request))
    context = {
        'form': form,
        'csrf_input': csrf_input,
    }
    return render_to_response('retake_catalog/retake_catalog_form.html', context)


