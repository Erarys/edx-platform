import jwt
from django.contrib.auth import login, authenticate, get_user_model
from django.http import HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from common.djangoapps.student.models import UserProfile
import requests

SECRET_KEY = "$ecRet@3#$2958GPIs!1"
User = get_user_model()

class UniverTestView(APIView):
    authentication_classes = []
    permission_classes = []

    @csrf_exempt
    def get(self, request):
        auth_token = request.GET.get('auth')
        if not auth_token:
            return Response({'error': 'Missing auth token'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = jwt.decode(auth_token, SECRET_KEY, algorithms=['HS256'])
            uname = decoded.get('uname')
            upwd = decoded.get('upwd')

            if not uname or not upwd:
                return Response({'error': 'Invalid token payload'}, status=status.HTTP_400_BAD_REQUEST)

            username = uname
            email_value = f"{username}@kaznu.edu.kz"
            surname, name, gender, stage, birth_year = decode_token_and_fetch_profile(auth_token)

            # Проверка пользователя
            user = User.objects.filter(username=username).first()

            if not user:
                # Создание нового
                user = User.objects.create(username=username, email=email_value)
                user.set_password(upwd)
                user.save()

                UserProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        'name': name,
                        'country': 'KZ',
                        'gender': gender, 
                        'level_of_education': stage,
                        'year_of_birth': birth_year,
                        'mailing_address': 'Kaznu',
                        
                    }
                )
            else:
                
                if not user.check_password(upwd):
                    user.set_password(upwd)
                    user.save()

            # Авторизация
            auth_user = authenticate(username=username, password=upwd)
            if auth_user is None:
                return Response({'error': 'Authentication failed'}, status=status.HTTP_401_UNAUTHORIZED)

            login(request, auth_user)
            return HttpResponseRedirect('/dashboard')

        except jwt.ExpiredSignatureError:
            return Response({'error': 'Token expired'}, status=status.HTTP_401_UNAUTHORIZED)
        except jwt.InvalidTokenError:
            return Response({'error': 'Invalid token'}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def decode_token_and_fetch_profile(token, secret_key='$ecRet@3#$2958GPIs!1'):
    try:
        decoded = jwt.decode(token, secret_key, algorithms=['HS256'])
    except jwt.InvalidSignatureError:
        raise SystemExit("Ошибка: неверная подпись JWT")
    except jwt.DecodeError as e:
        raise SystemExit("Ошибка при декодировании JWT: {0}".format(e))

    uname = decoded.get('uname')
    upwd  = decoded.get('upwd')
    if not uname or not upwd:
        raise SystemExit("Ошибка: в токене нет 'uname' или 'upwd'")

    session = requests.Session()
    resp = session.post(
        "https://univerapi.kaznu.kz/user/loginMoodle",
        data={'login': uname, 'password': upwd},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get('code') != 0:
        raise SystemExit("loginMoodle вернул: {0}".format(data.get('message', '')))

    # Сначала пробуем получить профиль студента
    student_resp = session.get("https://univerapi.kaznu.kz/student/profile")
    student_data = None
    if student_resp.status_code == 200:
        try:
            student_data = student_resp.json()
        except:
            pass
    
    # Если данные студента получены успешно
    if student_data and student_data.get('code') == 0 and 'data' in student_data:
        info_list, personal_list = student_data['data']
        
        def find_value(lst, key):
            for d in lst:
                if key in d and ':' in d[key]:
                    return d[key].split(':', 1)[1].strip()
            return None

        surname = find_value(personal_list, 'sname')
        name    = find_value(personal_list, 'name')
        
        raw_sex = find_value(personal_list, 'sex') or ''
        if isinstance(raw_sex, bytes):
            raw_sex = raw_sex.decode('utf-8')
        raw_sex = raw_sex.lower()

        if u'муж' in raw_sex:
            gender = 'm'
        elif u'жен' in raw_sex:
            gender = 'f'
        else:
            gender = 'o'

        raw_stage = find_value(info_list, 'stage') or ''
        if isinstance(raw_stage, bytes):
            raw_stage = raw_stage.decode('utf-8')
        raw_stage = raw_stage.lower()

        if u'бакалав' in raw_stage:
            stage = 'b'
        elif u'магис' in raw_stage:
            stage = 'm'
        elif u'доктор' in raw_stage:
            stage = 'p'
        else:
            stage = 'none'

        # Установка дефолтного года рождения для студента
        birth_year = 1995
        return surname, name, gender, stage, birth_year

    # Если не удалось получить данные студента, пробуем профиль преподавателя
    teacher_resp = session.get("https://univerapi.kaznu.kz/teacher/profile")
    if teacher_resp.status_code >= 500:
        raise SystemExit("teacher/profile 500:\n{0}".format(teacher_resp.text))
    teacher_resp.raise_for_status()
    teacher_data = teacher_resp.json()
    
    if teacher_data.get('code') != 0 or 'data' not in teacher_data:
        raise SystemExit("Не удалось получить данные ни студента, ни преподавателя")

    
    teacher_profile = teacher_data['data'][0]  
    surname = teacher_profile.get('sname', '')
    name = teacher_profile.get('name', '')
    gender = 'o'  
    stage = 'none' 
    
    
    birth_year = None
    if 'dateOfBirth' in teacher_profile:
        try:
            birth_year = int(teacher_profile['dateOfBirth'].split('.')[-1])
        except (ValueError, IndexError):
            pass

    return surname, name, gender, stage, birth_year
