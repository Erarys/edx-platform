# -*- coding: utf-8 -*-

from django.shortcuts import render
from django.http import HttpResponse
import logging
import jwt
import openedx.core.djangoapps.user_api.views as user_api_view
import requests
import django.middleware.csrf
from django.views.decorators.csrf import csrf_exempt, csrf_protect, ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.shortcuts import redirect
import re
secret = '$ecRet@3#$2958GPIs!1'

from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.generics import GenericAPIView

from univerapi.models import Univeruser
from random import randint
import json
import random
from student.models import User
import time
from django.db import connection
from django.contrib.sites.models import Site
from django.conf import settings
from django.http import JsonResponse

class UniverView(GenericAPIView):

    def createuser():
        return 123

    @method_decorator(csrf_protect)
    def post(self, request):
        return HttpResponse('post')
   
        auth = request.POST.get('auth')
        encoded_jwt = request.GET.get('auth')
        decoded = jwt.decode(encoded_jwt, "$ecRet@3#$2958GPIs!1", algorithms=["HS256"])

        new_user = Univeruser()
        created_user = ''
        created_user_email = ''
        try:
            exist_user = Univeruser.objects.get(univer_id=decoded['uname'])
            
            return HttpResponse('user exist')

        except:
	    surname, name, gender, stage, birth_year = decode_token_and_fetch_profile(encoded_jwt)
	    created_user = decoded['uname'].replace(".", "_") + str(random.randint(100, 999))
            created_user_email = decoded['uname'].replace(".", "_")+'@kaznu.edu.kz' 
            multipart_form_data = {
                'email': (None, created_user_email),
                'password': (None, decoded['upwd'][:75]),
                'name': (None, name),
                'username': (None, created_user),
                'level_of_education': (None, stage),
                'gender': (None, gender),
                'year_of_birth': (None, birth_year),
                'honor_code': (None, 'true'),
		'country': (None, 'KZ'),
		'goals': (None, 'education'),
		'surname': (None, surname),
		'mailing_address': (None, 'Kaznu')
            }
            client = requests.session()
            client.get('http://192.168.199.128', verify=False)
            csrftoken = client.cookies['csrftoken']
            login_data = dict(csrfmiddlewaretoken=csrftoken, next='/form_call/')
            coky = 'csrftoken='+csrftoken
            headers = {
               'X-CSRFToken': csrftoken,
               'Cookie':coky
            }
       
            response = requests.post('http://192.168.199.128:8181/user_api/v1/account/registration/', data=login_data, files=multipart_form_data, headers=headers, verify=False)

            json_data = json.loads(response.text)
            
            if ( ('success' in json_data) and (json_data['success'] == True)):
                created_user_str = str(created_user)
                
                exist_user = None
                i = 0
                while i < 60:
                    try:  
                        exist_user = User.objects.get(username=created_user_str)
                        User.refresh_from_db()
                        break
                    except:
                        i += 1    
                        time.sleep(3)
                        
                out_html=''    
                query = """SELECT id, username FROM auth_user where username LIKE '""" + "%%" + "hakymova" + "%%" + """'""" 
                query2 = """SELECT id, username FROM auth_user where username = '""" + created_user_str + """'"""


                return HttpResponse(repr(exist_user)) 

                time.sleep(10)
                cursor = connection.cursor()
                row = None
                sql = 'SELECT id, username FROM auth_user'
                try:
                    cursor.execute(str(query2))
                    row = cursor.fetchall()
                    return HttpResponse(repr(row)) 
                except Exception as e:
                    cursor.close
                    return HttpResponse('error =' + str(e)) 

                return HttpResponse(repr(cursor)) 
            else:
                return HttpResponse('no registration')    
            
            return HttpResponse((json_data['success']))


        return HttpResponse(repr(exist_user))

        multipart_form_data = {
	        'email': (None, 'dl@kaznu.kz'),
	        'password': (None, 'DlGao3wo'),
	        'remember': (None, 'true')
        }
        client = requests.session()
        client.get('http://192.168.199.128')
        csrftoken = client.cookies['csrftoken']
        login_data = dict(csrfmiddlewaretoken=csrftoken, next='/form_call/')
        coky = 'csrftoken='+csrftoken
        headers = {
            'X-CSRFToken': csrftoken,
            'Cookie':coky
        }
       
        response = requests.post('http://192.168.199.128/user_api/v1/account/login_session/', data=login_data, files=multipart_form_data, headers=headers, verify = False)

        headers2 = response.headers
        
        csrfToken = re.search(r'csrftoken=(\w+);', headers2['Set-Cookie'])
        sessionid = re.search(r'sessionid="(\S+)";', headers2['Set-Cookie'])


        cookies2 = {
        '_ga': 'GA1.1.1602606957.1602045613',
        'experiments_is_enterprise': 'false',
        'openedx-language-preference': 'ru',
        'csrftoken': csrfToken.group(1),
        'edxloggedin': 'true',
        'sessionid': sessionid.group(1),
        'edx-user-info': '{\\"username\\": \\"staff\\"\\054 \\"version\\": 1\\054 \\"enrollmentStatusHash\\": \\"ace9f6124341dc7692d41de5b91bff7d\\"\\054 \\"header_urls\\": {\\"learner_profile\\": \\"http://192.168.199.128/u/staff\\"\\054 \\"resume_block\\": \\"http://192.168.199.128/user_api/v1/account/login_session/\\"\\054 \\"logout\\": \\"http://192.168.199.128/logout\\"\\054 \\"account_settings\\": \\"http://192.168.199.128/account/settings\\"}}',
        }

        headers3 = {
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'Referer': 'http://192.168.199.128/login?next=%2F',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'sec-gpc': '1',
        }

        response = requests.get('http://192.168.199.128', headers=headers3, cookies=cookies2, verify=False)
        return HttpResponse(response)


    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        current_site = 'https://open.kaznu.kz'        

        auth = request.POST.get('auth')
        encoded_jwt = request.GET.get('auth')
        decoded = jwt.decode(encoded_jwt, secret, algorithms=["HS256"])
        created_user = ''
        created_user_email = ''

        try:
            exist_user = Univeruser.objects.get(univer_id=decoded['uname'])
            
            return render(request, 'univerapi/redirect.html')
	
        except:
	    surname, name, gender, stage, birth_year = decode_token_and_fetch_profile(encoded_jwt)
	    created_user = decoded['uname'].replace(".", "_") + str(random.randint(100, 999))

            created_user_email = decoded['uname'].replace(".", "_")+'@kaznu.edu.kz'
            multipart_form_data = {
                'email': (None, created_user_email),
                'password': (None, decoded['upwd'][:75]),
                'name': (None, name),
                'username': (None, created_user),
                'level_of_education': (None, stage),
                'gender': (None, gender),
                'year_of_birth': (None, birth_year),
                'honor_code': (None, 'true'),
		'country': (None , 'KZ'),
		'surname': (None, surname),
		'goals': (None, 'education'),
		'mailing_address': (None, 'Kaznu')
            }
            client = requests.session()
            client.get(current_site, verify = False)
            csrftoken = client.cookies['csrftoken']
            login_data = dict(csrfmiddlewaretoken=csrftoken, next='/form_call/')
            coky = 'csrftoken='+csrftoken
            headers = {
               'X-CSRFToken': csrftoken,
               'Cookie':coky
            }
           
            count_try = 0
            while True:
                response = requests.post(current_site + '/user_api/v1/account/registration/', data=login_data, files=multipart_form_data, headers=headers, verify = False)
                json_data = json.loads(response.text)
                if ( ('success' in json_data) and (json_data['success'] == True)):
                    break
                if ( ('username' in json_data) ):    
                    if "existing account" in str(json_data): 
                       

                        return render(request, 'univerapi/redirect.html')
                time.sleep(3)   
                count_try += 1
                if (count_try == 20):
                    return JsonResponse(json_data)       
            
            if ( ('success' in json_data) and (json_data['success'] == True)):
                new_user = Univeruser()
                new_user.edx_username = created_user
                new_user.edx_id=0
                new_user.edx_pass='0'
                new_user.univer_id = decoded['uname']
                new_user.save()                
                return render(request, 'univerapi/redirect.html')
                created_user_str = str(created_user)
                
                exist_user = None
                i = 0
                while i < 60:
                    try:  
                        exist_user = User.objects.get(username=created_user_str)
                        User.refresh_from_db()
                        break
                    except:
                        i += 1    
                        time.sleep(3)
                        
                out_html=''    
                query = """SELECT id, username FROM auth_user where username LIKE '""" + "%%" + "hakymova" + "%%" + """'""" 
                query2 = """SELECT id, username FROM auth_user where username = '""" + created_user_str + """'"""

                return HttpResponse(repr(exist_user)) 

                time.sleep(10)
                cursor = connection.cursor()
                row = None
                sql = 'SELECT id, username FROM auth_user'
                try:
                    cursor.execute(str(query2))
                    row = cursor.fetchall()
                    return HttpResponse(repr(row)) 
                except Exception as e:
                    cursor.close
                    return HttpResponse('error =' + str(e)) 

                return HttpResponse(repr(cursor))     
            else:
                return HttpResponse('no registration')


def authview(request):
	return render(request, 'univerapi/index.html')


def authview_check(request):
    auth = request.POST.get('auth')
    auth = auth.split("auth=")[1]
    encoded_jwt = auth
    decoded = jwt.decode(encoded_jwt, "$ecRet@3#$2958GPIs!1", algorithms=["HS256"])
    already_exist_user = decoded['uname']

    try:
        exist_user = Univeruser.objects.get(univer_id=decoded['uname'])
        exist_edx_user = User.objects.get(username=exist_user.edx_username)
        exist_edx_user.is_active = 1
        exist_edx_user.save()

    except:
        return JsonResponse({"success":"false"})

    return JsonResponse({"success":"true"})


# -*- coding: utf-8 -*-
import requests
import jwt

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

    # Обработка данных преподавателя
    teacher_profile = teacher_data['data'][0]  # Первый элемент в списке данных
    surname = teacher_profile.get('sname', '')
    name = teacher_profile.get('name', '')
    gender = 'o'  # По умолчанию, если поле отсутствует
    stage = 'none'  # Для преподавателя ступень не применима
    
    # Извлечение года рождения из даты (формат "дд.мм.гггг")
    birth_year = None
    if 'dateOfBirth' in teacher_profile:
        try:
            birth_year = int(teacher_profile['dateOfBirth'].split('.')[-1])
        except (ValueError, IndexError):
            pass

    return surname, name, gender, stage, birth_year
