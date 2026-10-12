"""Authentication endpoints for the accounts app.

Each view declares its own permission explicitly:
- register/login: AllowAny (the only two public endpoints)
- logout/me: IsAuthenticated (protected)

CSRF note: DRF's SessionAuthentication re-enforces CSRF on any request where
a session user is present, so logout/me are CSRF-protected. We never disable
CsrfViewMiddleware.
"""

from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .serializers import LoginSerializer, RegisterSerializer, UserSerializer

# Generic message shared by every login failure: never reveals whether the
# username exists (user-enumeration protection).
INVALID_CREDENTIALS = "Invalid username or password."


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    """POST /api/auth/register/ -- create a new account (201)."""
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.create(serializer.validated_data)
    return Response(
        {"detail": "Account created.", "user": UserSerializer(user).data},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    """POST /api/auth/login/ -- authenticate and open a session (200)."""
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = authenticate(
        request,
        username=serializer.validated_data["username"],
        password=serializer.validated_data["password"],
    )

    # authenticate() returns None for unknown user AND wrong password --
    # one generic 401 covers both, so responses reveal nothing.
    if user is None:
        return Response(
            {"detail": INVALID_CREDENTIALS},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    login(request, user)  # creates the session + sessionid cookie
    return Response({"detail": "Logged in.", "user": UserSerializer(user).data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    """POST /api/auth/logout/ -- destroy the session (200)."""
    logout(request)  # flushes the session server-side and rotates the cookie
    return Response({"detail": "Logged out."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    """GET /api/auth/me/ -- current user (200 / 403)."""
    return Response(UserSerializer(request.user).data)


@ensure_csrf_cookie
def csrf(request):
    """GET /api/auth/csrf/ -- sets the csrftoken cookie for clients.

    Plain Django view on purpose: the React frontend / Postman call this
    once, read the cookie value, and echo it back as the X-CSRFToken header
    on POST/PUT/DELETE requests.
    """
    return JsonResponse({"detail": "CSRF cookie set."})
