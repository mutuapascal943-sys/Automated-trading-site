from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from .models import User

BROKER_CHOICES = [
    ('', 'Choose your broker'),
    ('Deriv', 'Deriv'),
    ('Exness', 'Exness'),
    ('XM', 'XM'),
    ('FBS', 'FBS'),
    ('HFM (HotForex)', 'HFM (HotForex)'),
    ('IC Markets', 'IC Markets'),
    ('Pepperstone', 'Pepperstone'),
    ('FXTM', 'FXTM'),
    ('Tickmill', 'Tickmill'),
    ('RoboForex', 'RoboForex'),
]


class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-input', 'placeholder': 'you@example.com', 'id': 'reg-email'
        })
    )
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'placeholder': 'Min 8 characters', 'id': 'reg-pass'
        })
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'placeholder': 'Repeat password', 'id': 'reg-pass2'
        })
    )
    broker = forms.ChoiceField(
        choices=BROKER_CHOICES, required=True,
        widget=forms.Select(attrs={'class': 'form-input', 'id': 'reg-broker'})
    )

    class Meta:
        model = User
        fields = ('email', 'password1', 'password2', 'broker')

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('A user with this email already exists.')
        return email.lower()


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-input', 'placeholder': 'you@example.com', 'id': 'login-email'
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'placeholder': 'Your password', 'id': 'login-pass'
        })
    )

    def clean_username(self):
        return self.cleaned_data.get('username').lower()


class OTPForm(forms.Form):
    otp_code = forms.CharField(
        label='Authentication Code',
        max_length=6, min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': '000000',
            'id': 'otp-code', 'autocomplete': 'off',
            'inputmode': 'numeric', 'pattern': '[0-9]*'
        })
    )
