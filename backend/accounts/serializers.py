"""Serializers for the accounts app.

Serializers do two jobs:
1. Validate/normalize incoming data (write path).
2. Shape outgoing data (read path).

Only declared fields are ever serialized, so passwords can never leak
through UserSerializer -- they are simply not part of it.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Safe, read-only representation of a user.

    Note: 'password' is deliberately absent. ModelSerializer would only
    include it if we asked for it.
    """

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'date_joined', 'last_login')
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    """Validates registration input and creates the user."""

    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_username(self, value):
        """Reject duplicates. Exact match mirrors Django's own username rules."""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Username is required.")
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def validate_email(self, value):
        """Normalize (lowercase/strip) then reject duplicates case-insensitively."""
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value

    def validate(self, attrs):
        """Cross-field checks: confirmation match + Django password validators."""
        password = attrs.get('password', '')
        password_confirm = attrs.get('password_confirm', '')

        if password != password_confirm:
            raise serializers.ValidationError(
                {'password_confirm': 'Passwords do not match.'}
            )

        # Pass an unsaved user so validators like UserAttributeSimilarityValidator
        # can compare the password against username/email.
        candidate = User(
            username=attrs.get('username', ''),
            email=attrs.get('email', ''),
        )
        try:
            validate_password(password, user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)})

        return attrs

    def create(self, validated_data):
        """Hash with set_password() -- never store plain text."""
        validated_data.pop('password_confirm')
        user = User(
            username=validated_data['username'],
            email=validated_data['email'],
        )
        user.set_password(validated_data['password'])  # bcrypt/pbkdf2 hash
        user.save()
        return user


class LoginSerializer(serializers.Serializer):
    """Credentials only -- both fields write_only so they are never echoed back."""

    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
