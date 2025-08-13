import jwt
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from openedx.core.djangoapps.user_authn.views.login import login_user  # Assuming this is importable as per your provided code
SECRET_KEY = "$ecRet@3#$2958GPIs!1"
class UniverTestView(APIView):
    authentication_classes = []  # Allow anonymous access since it's a redirect from external auth
    permission_classes = []

    @csrf_exempt  # If CSRF is an issue in this redirect flow; adjust based on your setup
    def get(self, request):
        auth_token = request.GET.get('auth')
        if not auth_token:
            return Response({'error': 'Missing auth token'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Decode JWT (replace with your actual secret)
            decoded = jwt.decode(auth_token, SECRET_KEY, algorithms=['HS256'])
            uname = decoded.get('uname')
            upwd = decoded.get('upwd')

            if not uname or not upwd:
                return Response({'error': 'Invalid token payload'}, status=status.HTTP_400_BAD_REQUEST)

            # Modify uname: replace '.' with '_'
            modified_uname = uname.replace('.', '_')

            # Check if user exists
            user, created = User.objects.get_or_create(username=modified_uname)

            # Set or update password (handles updates)
            user.set_password(upwd)
            user.save()

            # If new user, set additional fields if needed (e.g., email, but minimal for now)
            if created:
                # Optionally set email or other profile fields; e.g., user.email = f"{modified_uname}@kaznu.edu.kz"
                user.save()

            # Authenticate and login
            # Simulate the login_user call (adapt api_version as needed, e.g., 'v1')
            login_response = login_user(request, 'v1')  # This assumes login_user handles authentication
            if login_response.status_code != status.HTTP_200_OK:
                return login_response  # Return error if login fails

            # Manually login the user in the session if needed (in case login_user doesn't)
            login(request, user)

            # Redirect to main page (e.g., dashboard)
            return HttpResponseRedirect('/dashboard')

        except jwt.ExpiredSignatureError:
            return Response({'error': 'Token expired'}, status=status.HTTP_401_UNAUTHORIZED)
        except jwt.InvalidTokenError:
            return Response({'error': 'Invalid token'}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
