import jwt
from django.contrib.auth import login, authenticate
from django.contrib.auth.models import User
from django.http import HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from student.models import UserProfile  # Путь для Open edX

SECRET_KEY = "$ecRet@3#$2958GPIs!1"

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

            modified_uname = uname.replace('.', '_')

            user, created = User.objects.get_or_create(
                username=modified_uname,
                defaults={'email': f"{modified_uname}@kaznu.edu.kz"}
            )

            # Создаём или обновляем профиль
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.name = uname  # Заполняем поле name
            profile.save()

            # Обновляем пароль
            user.set_password(upwd)
            user.save()

            # Аутентификация
            auth_user = authenticate(username=modified_uname, password=upwd)
            if auth_user is None:
                return Response({'error': 'Authentication failed'}, status=status.HTTP_401_UNAUTHORIZED)

            # Логиним
            login(request, auth_user)

            # Редирект на дашборд
            return HttpResponseRedirect('/dashboard')

        except jwt.ExpiredSignatureError:
            return Response({'error': 'Token expired'}, status=status.HTTP_401_UNAUTHORIZED)
        except jwt.InvalidTokenError:
            return Response({'error': 'Invalid token'}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
