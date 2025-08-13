import jwt
from django.contrib.auth import login, authenticate, get_user_model
from django.http import HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from common.djangoapps.student.models import UserProfile

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
                        'name': username,
                        'country': 'KZ',
                        'gender': 'f', 
                        'level_of_education': 'b',
                        'year_of_birth': '2002',
                        
                    }
                )
            else:
                # Обновление пароля, если он другой
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
